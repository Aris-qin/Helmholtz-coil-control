/**
 * 可控磁场系统 - ESP32-S3 固件 (ESP-IDF)
 *
 * 对齐 PC 端协议版本：
 *   VIBE:<axis>,<freq>,<current>
 *   VIBE_STOP
 *   SET_PHASE:<X>,<Y>,<Z>      // 数值角度 or OFF/NONE/NaN
 *   ROTATE:<freq>,<current>
 *   ROTATE_STOP
 *   STATUS
 *   ALL_STOP
 *
 * 说明：
 * - 10kHz 波形更新
 * - 查表正弦波
 * - 运行中可实时 SET_PHASE，不重置基相位
 * - 三轴独立开关/相位控制
 * - 4 通道通过 LDAC 同步锁存
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <ctype.h>
#include <strings.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"

#include "esp_log.h"
#include "esp_err.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ============================================================
// 配置参数
// ============================================================
#define SERIAL_BAUD         115200
#define UART_PORT           UART_NUM_0
#define UART_TX_PIN         GPIO_NUM_43
#define UART_RX_PIN         GPIO_NUM_44
#define UART_BUF_SIZE       1024
#define CMD_BUF_SIZE        128
#define WAVE_NOTIFY_BIT    (1UL << 0)
#define SAMPLE_RATE_HZ      10000
#define WAVE_UPDATE_US      100
#define TABLE_SIZE          1024   // 必须是 2^n，便于掩码取模

#define MAX_CURRENT_A       5.0f
#define MIN_FREQ_HZ         0.01f
#define MAX_FREQ_HZ         5000.0f

// DAC8555
#define PIN_DAC_SYNC        GPIO_NUM_4
#define PIN_DAC_SCLK        GPIO_NUM_5
#define PIN_DAC_DIN         GPIO_NUM_6
#define PIN_DAC_LDAC        GPIO_NUM_7

#define DAC_CH_X            0
#define DAC_CH_Y            1
#define DAC_CH_Z            2
#define DAC_CH_W            3

// 原代码中的近似换算，实际仍建议按硬件标定
#define DAC_VREF            2.5f
#define OPA549_GAIN         3.0f

#define SOFT_STOP_STEPS     32
#define SOFT_STOP_DELAY_US  200

// ============================================================
// 类型 / 全局状态
// ============================================================
static const char *TAG = "COIL";

typedef enum {
    MODE_NONE = 0,
    MODE_VIBRATE,
    MODE_ROTATE
} coil_mode_t;

typedef struct {
    coil_mode_t mode;
    bool running;

    // 通用参数
    float frequency;
    float current_amps;
    uint16_t current_code;

    // VIBRATE
    int vibe_axis;   // 0:X 1:Y 2:Z, -1:none

    // ROTATE
    bool axis_enable[3];     // X/Y/Z
    float phase_deg[3];      // 仅用于状态显示
    uint16_t phase_idx[3];   // 查表偏移

    // 实际输出缓存
    uint16_t output[4];
} coil_state_t;

static spi_device_handle_t spi_handle = NULL;
static TaskHandle_t wave_task_handle = NULL;
static esp_timer_handle_t wave_timer = NULL;

static coil_state_t state = {
    .mode = MODE_NONE,
    .running = false,
    .frequency = 0.0f,
    .current_amps = 0.0f,
    .current_code = 0,
    .vibe_axis = -1,
    .axis_enable = { true, true, false },
    .phase_deg = { 0.0f, 90.0f, 0.0f },
    .phase_idx = { 0, TABLE_SIZE / 4, 0 },
    .output = { 0, 0, 0, 0 }
};

static uint16_t sin_table[TABLE_SIZE];
static uint32_t phase_acc = 0;
static uint32_t phase_step = 0;

// 双核共享状态保护
static portMUX_TYPE state_lock = portMUX_INITIALIZER_UNLOCKED;

// ============================================================
// 工具函数
// ============================================================
static char *trim_inplace(char *s)
{
    while (*s && isspace((unsigned char)*s)) s++;
    char *end = s + strlen(s);
    while (end > s && isspace((unsigned char)*(end - 1))) {
        *(--end) = '\0';
    }
    return s;
}

static void uart_sendf(const char *prefix, const char *fmt, ...)
{
    char body[256];
    char line[300];

    va_list ap;
    va_start(ap, fmt);
    vsnprintf(body, sizeof(body), fmt, ap);
    va_end(ap);

    snprintf(line, sizeof(line), "%s:%s\r\n", prefix, body);
    uart_write_bytes(UART_PORT, line, strlen(line));
}

static void send_ok(const char *fmt, ...)
{
    char body[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(body, sizeof(body), fmt, ap);
    va_end(ap);
    uart_sendf("OK", "%s", body);
}

static void send_err(const char *fmt, ...)
{
    char body[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(body, sizeof(body), fmt, ap);
    va_end(ap);
    uart_sendf("ERR", "%s", body);
}

static uint16_t current_to_code(float amps)
{
    if (amps <= 0.0f) return 0;
    if (amps > MAX_CURRENT_A) amps = MAX_CURRENT_A;

    float code_f = (amps * 65535.0f) / (DAC_VREF * OPA549_GAIN);
    if (code_f < 0.0f) code_f = 0.0f;
    if (code_f > 65535.0f) code_f = 65535.0f;
    return (uint16_t)(code_f + 0.5f);
}

static uint16_t phase_deg_to_index(float deg)
{
    while (deg < 0.0f)   deg += 360.0f;
    while (deg >= 360.0f) deg -= 360.0f;

    uint32_t idx = (uint32_t)((deg / 360.0f) * TABLE_SIZE + 0.5f);
    return (uint16_t)(idx & (TABLE_SIZE - 1));
}

static uint32_t freq_to_phase_step(float freq_hz)
{
    double step = ((double)freq_hz * (double)TABLE_SIZE / (double)SAMPLE_RATE_HZ) * 65536.0;
    if (step < 0.0) step = 0.0;
    if (step > 4294967295.0) step = 4294967295.0;
    return (uint32_t)(step + 0.5);
}

static bool parse_phase_token(const char *tok, bool *enable, float *deg_out, uint16_t *idx_out)
{
    if (!tok || !enable || !deg_out || !idx_out) return false;

    char tmp[32];
    snprintf(tmp, sizeof(tmp), "%s", tok);
    char *s = trim_inplace(tmp);

    if (strcasecmp(s, "OFF") == 0 ||
        strcasecmp(s, "NONE") == 0 ||
        strcasecmp(s, "NAN") == 0) {
        *enable = false;
        *deg_out = 0.0f;
        *idx_out = 0;
        return true;
    }

    char *endptr = NULL;
    float deg = strtof(s, &endptr);
    if (endptr == s) {
        return false;
    }

    while (deg < 0.0f)   deg += 360.0f;
    while (deg >= 360.0f) deg -= 360.0f;

    *enable = true;
    *deg_out = deg;
    *idx_out = phase_deg_to_index(deg);
    return true;
}

static void format_phase(char *buf, size_t len, bool enable, float deg)
{
    if (!enable) {
        snprintf(buf, len, "OFF");
    } else {
        snprintf(buf, len, "%.1f", deg);
    }
}

// ============================================================
// 查表正弦波
// ============================================================
static void init_sin_table(void)
{
    for (int i = 0; i < TABLE_SIZE; i++) {
        float s = (sinf(2.0f * (float)M_PI * (float)i / (float)TABLE_SIZE) + 1.0f) * 0.5f;
        sin_table[i] = (uint16_t)(s * 65535.0f + 0.5f);
    }
    ESP_LOGI(TAG, "Sine table initialized, TABLE_SIZE=%d", TABLE_SIZE);
}

// ============================================================
// DAC8555
// ============================================================
static void dac_init(void)
{
    spi_bus_config_t bus_cfg = {
        .mosi_io_num = PIN_DAC_DIN,
        .miso_io_num = -1,
        .sclk_io_num = PIN_DAC_SCLK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 4,
    };

    spi_device_interface_config_t dev_cfg = {
        .mode = 0,
        .clock_speed_hz = 20 * 1000 * 1000,
        .spics_io_num = PIN_DAC_SYNC,
        .queue_size = 8,
        .flags = SPI_DEVICE_3WIRE,
    };

    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO));
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev_cfg, &spi_handle));
    ESP_ERROR_CHECK(gpio_set_direction(PIN_DAC_LDAC, GPIO_MODE_OUTPUT));

    gpio_set_level(PIN_DAC_LDAC, 1);
    vTaskDelay(pdMS_TO_TICKS(10));

    ESP_LOGI(TAG, "DAC initialized");
}

static inline void dac_update(const uint16_t val[4])
{
    spi_transaction_t t[4];
    uint8_t buf[4][3];
    memset(t, 0, sizeof(t));
    memset(buf, 0, sizeof(buf));

    for (int ch = 0; ch < 4; ch++) {
        // 与你原始代码一致的 24-bit 格式
        buf[ch][0] = ((ch & 0x03) << 4);
        buf[ch][1] = (uint8_t)(val[ch] >> 8);
        buf[ch][2] = (uint8_t)(val[ch] & 0xFF);

        t[ch].length = 24;
        t[ch].tx_buffer = buf[ch];
        ESP_ERROR_CHECK(spi_device_queue_trans(spi_handle, &t[ch], portMAX_DELAY));
    }

    spi_transaction_t *rt = NULL;
    for (int i = 0; i < 4; i++) {
        ESP_ERROR_CHECK(spi_device_get_trans_result(spi_handle, &rt, portMAX_DELAY));
    }

    gpio_set_level(PIN_DAC_LDAC, 0);
    esp_rom_delay_us(1);
    gpio_set_level(PIN_DAC_LDAC, 1);
}

static void dac_zero_all(void)
{
    const uint16_t zero[4] = {0, 0, 0, 0};
    dac_update(zero);

    taskENTER_CRITICAL(&state_lock);
    memset(state.output, 0, sizeof(state.output));
    taskEXIT_CRITICAL(&state_lock);
}

// ============================================================
// 停止控制
// ============================================================
static void soft_stop_outputs(bool clear_mode)
{
    uint16_t start[4];
    bool was_running = false;

    taskENTER_CRITICAL(&state_lock);
    was_running = state.running || (state.mode != MODE_NONE);
    state.running = false;
    for (int i = 0; i < 4; i++) {
        start[i] = state.output[i];
    }
    taskEXIT_CRITICAL(&state_lock);

    if (was_running) {
        for (int step = SOFT_STOP_STEPS; step >= 0; step--) {
            uint16_t frame[4];
            for (int ch = 0; ch < 4; ch++) {
                frame[ch] = (uint16_t)(((uint32_t)start[ch] * (uint32_t)step) / SOFT_STOP_STEPS);
            }
            dac_update(frame);
            esp_rom_delay_us(SOFT_STOP_DELAY_US);
        }
    } else {
        dac_zero_all();
    }

    taskENTER_CRITICAL(&state_lock);
    memset(state.output, 0, sizeof(state.output));
    if (clear_mode) {
        state.mode = MODE_NONE;
    }
    taskEXIT_CRITICAL(&state_lock);
}

static void emergency_stop(void)
{
    taskENTER_CRITICAL(&state_lock);
    state.running = false;
    state.mode = MODE_NONE;
    memset(state.output, 0, sizeof(state.output));
    taskEXIT_CRITICAL(&state_lock);

    dac_zero_all();
}

// ============================================================
// 定时器 / 波形任务
// ============================================================
static void IRAM_ATTR timer_cb(void *arg)
{
    (void)arg;

    if (wave_task_handle == NULL) {
        return;
    }

    BaseType_t hp_task_woken = pdFALSE;
    xTaskNotifyFromISR(wave_task_handle, WAVE_NOTIFY_BIT, eSetBits, &hp_task_woken);

    if (hp_task_woken) {
        portYIELD_FROM_ISR();
    }
}

static void wave_task(void *arg)
{
    (void)arg;
    ESP_LOGI(TAG, "Wave task started");

   while (1) {
    uint32_t notify_val = 0;
    xTaskNotifyWait(0, UINT32_MAX, &notify_val, portMAX_DELAY);

    if ((notify_val & WAVE_NOTIFY_BIT) == 0) {
        continue;
    }

        coil_mode_t mode;
        bool running;
        int vibe_axis;
        uint16_t current_code;
        bool axis_enable[3];
        uint16_t phase_idx_local[3];
        uint16_t out[4] = {0, 0, 0, 0};
        uint16_t base_idx;

        taskENTER_CRITICAL(&state_lock);
        running = state.running;
        mode = state.mode;
        vibe_axis = state.vibe_axis;
        current_code = state.current_code;

        axis_enable[0] = state.axis_enable[0];
        axis_enable[1] = state.axis_enable[1];
        axis_enable[2] = state.axis_enable[2];

        phase_idx_local[0] = state.phase_idx[0];
        phase_idx_local[1] = state.phase_idx[1];
        phase_idx_local[2] = state.phase_idx[2];

        phase_acc += phase_step;
        base_idx = (uint16_t)((phase_acc >> 16) & (TABLE_SIZE - 1));
        taskEXIT_CRITICAL(&state_lock);

        if (!running) {
            continue;
        }

        if (mode == MODE_VIBRATE) {
            uint16_t val = (uint16_t)(((uint32_t)sin_table[base_idx] * (uint32_t)current_code) >> 16);
            if (vibe_axis >= 0 && vibe_axis <= 2) {
                out[vibe_axis] = val;
            }
            out[DAC_CH_W] = 0;
        }
        else if (mode == MODE_ROTATE) {
            for (int ch = 0; ch < 3; ch++) {
                if (axis_enable[ch]) {
                    uint16_t idx = (uint16_t)((base_idx + phase_idx_local[ch]) & (TABLE_SIZE - 1));
                    out[ch] = (uint16_t)(((uint32_t)sin_table[idx] * (uint32_t)current_code) >> 16);
                } else {
                    out[ch] = 0;
                }
            }
            out[DAC_CH_W] = 0;
        }
        else {
            memset(out, 0, sizeof(out));
        }

        dac_update(out);

        taskENTER_CRITICAL(&state_lock);
        memcpy(state.output, out, sizeof(out));
        taskEXIT_CRITICAL(&state_lock);
    }
}

// ============================================================
// 命令处理
// ============================================================
static void process_cmd(const char *raw_cmd)
{
    char cmd[CMD_BUF_SIZE];
    snprintf(cmd, sizeof(cmd), "%s", raw_cmd);
    char *s = trim_inplace(cmd);

    // --------------------------------------------------------
    // VIBE:<axis>,<freq>,<current>
    // --------------------------------------------------------
    if (strncasecmp(s, "VIBE:", 5) == 0) {
        char args[CMD_BUF_SIZE];
        snprintf(args, sizeof(args), "%s", s + 5);

        char *save = NULL;
        char *tok_axis = strtok_r(args, ",", &save);
        char *tok_freq = strtok_r(NULL, ",", &save);
        char *tok_curr = strtok_r(NULL, ",", &save);

        if (!tok_axis || !tok_freq || !tok_curr) {
            send_err("VIBE format: VIBE:<axis>,<freq>,<current>");
            return;
        }

        tok_axis = trim_inplace(tok_axis);
        tok_freq = trim_inplace(tok_freq);
        tok_curr = trim_inplace(tok_curr);

        char axis = (char)toupper((unsigned char)tok_axis[0]);
        int axis_idx = -1;
        if (axis == 'X') axis_idx = 0;
        else if (axis == 'Y') axis_idx = 1;
        else if (axis == 'Z') axis_idx = 2;
        else {
            send_err("Invalid axis, use X/Y/Z");
            return;
        }

        float freq = strtof(tok_freq, NULL);
        float curr = strtof(tok_curr, NULL);

        if (freq < MIN_FREQ_HZ || freq > MAX_FREQ_HZ) {
            send_err("Freq out of range %.2f~%.2f Hz", MIN_FREQ_HZ, MAX_FREQ_HZ);
            return;
        }
        if (curr <= 0.0f || curr > MAX_CURRENT_A) {
            send_err("Current out of range (0~%.2f A]", MAX_CURRENT_A);
            return;
        }

        soft_stop_outputs(false);

        taskENTER_CRITICAL(&state_lock);
        state.mode = MODE_VIBRATE;
        state.running = true;
        state.vibe_axis = axis_idx;
        state.frequency = freq;
        state.current_amps = curr;
        state.current_code = current_to_code(curr);

        phase_acc = 0;
        phase_step = freq_to_phase_step(freq);
        taskEXIT_CRITICAL(&state_lock);

        send_ok("Vibrate axis=%c freq=%.2fHz curr=%.2fA", axis, freq, curr);
        return;
    }

    // --------------------------------------------------------
    // VIBE_STOP
    // --------------------------------------------------------
    if (strcasecmp(s, "VIBE_STOP") == 0) {
        soft_stop_outputs(true);
        send_ok("Vibrate stopped");
        return;
    }

    // --------------------------------------------------------
    // SET_PHASE:<X>,<Y>,<Z>
    // 支持 OFF/NONE/NaN
    // 例如:
    //   SET_PHASE:0,90,OFF
    //   SET_PHASE:OFF,0,90
    // --------------------------------------------------------
    if (strncasecmp(s, "SET_PHASE:", 10) == 0) {
        char args[CMD_BUF_SIZE];
        snprintf(args, sizeof(args), "%s", s + 10);

        char *save = NULL;
        char *tok_x = strtok_r(args, ",", &save);
        char *tok_y = strtok_r(NULL, ",", &save);
        char *tok_z = strtok_r(NULL, ",", &save);

        if (!tok_x || !tok_y || !tok_z) {
            send_err("SET_PHASE format: SET_PHASE:<X>,<Y>,<Z>");
            return;
        }

        bool en[3];
        float deg[3];
        uint16_t idx[3];

        if (!parse_phase_token(tok_x, &en[0], &deg[0], &idx[0]) ||
            !parse_phase_token(tok_y, &en[1], &deg[1], &idx[1]) ||
            !parse_phase_token(tok_z, &en[2], &deg[2], &idx[2])) {
            send_err("Invalid phase token, use number or OFF/NONE/NaN");
            return;
        }

        taskENTER_CRITICAL(&state_lock);
        for (int i = 0; i < 3; i++) {
            state.axis_enable[i] = en[i];
            state.phase_deg[i] = deg[i];
            state.phase_idx[i] = idx[i];
        }
        taskEXIT_CRITICAL(&state_lock);

        char px[16], py[16], pz[16];
        format_phase(px, sizeof(px), en[0], deg[0]);
        format_phase(py, sizeof(py), en[1], deg[1]);
        format_phase(pz, sizeof(pz), en[2], deg[2]);

        send_ok("Phase X=%s Y=%s Z=%s", px, py, pz);
        return;
    }

    // --------------------------------------------------------
    // ROTATE:<freq>,<current>
    // --------------------------------------------------------
    if (strncasecmp(s, "ROTATE:", 7) == 0) {
        char args[CMD_BUF_SIZE];
        snprintf(args, sizeof(args), "%s", s + 7);

        char *save = NULL;
        char *tok_freq = strtok_r(args, ",", &save);
        char *tok_curr = strtok_r(NULL, ",", &save);

        if (!tok_freq || !tok_curr) {
            send_err("ROTATE format: ROTATE:<freq>,<current>");
            return;
        }

        tok_freq = trim_inplace(tok_freq);
        tok_curr = trim_inplace(tok_curr);

        float freq = strtof(tok_freq, NULL);
        float curr = strtof(tok_curr, NULL);

        if (freq < MIN_FREQ_HZ || freq > MAX_FREQ_HZ) {
            send_err("Freq out of range %.2f~%.2f Hz", MIN_FREQ_HZ, MAX_FREQ_HZ);
            return;
        }
        if (curr <= 0.0f || curr > MAX_CURRENT_A) {
            send_err("Current out of range (0~%.2f A]", MAX_CURRENT_A);
            return;
        }

        bool any_axis = false;
        taskENTER_CRITICAL(&state_lock);
        any_axis = state.axis_enable[0] || state.axis_enable[1] || state.axis_enable[2];
        taskEXIT_CRITICAL(&state_lock);

        if (!any_axis) {
            send_err("No axis enabled, call SET_PHASE first");
            return;
        }

        soft_stop_outputs(false);

        taskENTER_CRITICAL(&state_lock);
        state.mode = MODE_ROTATE;
        state.running = true;
        state.frequency = freq;
        state.current_amps = curr;
        state.current_code = current_to_code(curr);

        phase_acc = 0;
        phase_step = freq_to_phase_step(freq);
        taskEXIT_CRITICAL(&state_lock);

        send_ok("Rotate freq=%.2fHz curr=%.2fA", freq, curr);
        return;
    }

    // --------------------------------------------------------
    // ROTATE_STOP
    // --------------------------------------------------------
    if (strcasecmp(s, "ROTATE_STOP") == 0) {
        soft_stop_outputs(true);
        send_ok("Rotate stopped");
        return;
    }

    // --------------------------------------------------------
    // STATUS
    // --------------------------------------------------------
    if (strcasecmp(s, "STATUS") == 0) {
        coil_state_t snap;
        uint32_t step_snap;
        taskENTER_CRITICAL(&state_lock);
        snap = state;
        step_snap = phase_step;
        taskEXIT_CRITICAL(&state_lock);

        char px[16], py[16], pz[16];
        format_phase(px, sizeof(px), snap.axis_enable[0], snap.phase_deg[0]);
        format_phase(py, sizeof(py), snap.axis_enable[1], snap.phase_deg[1]);
        format_phase(pz, sizeof(pz), snap.axis_enable[2], snap.phase_deg[2]);

        send_ok(
            "Mode=%s|Run=%s|Freq=%.2f|Curr=%.2f|Step=%lu|"
            "PhX=%s|PhY=%s|PhZ=%s|"
            "OutX=%u|OutY=%u|OutZ=%u|OutW=%u",
            (snap.mode == MODE_VIBRATE) ? "VIBRATE" :
            (snap.mode == MODE_ROTATE)  ? "ROTATE"  : "NONE",
            snap.running ? "YES" : "NO",
            snap.frequency,
            snap.current_amps,
            (unsigned long)step_snap,
            px, py, pz,
            snap.output[0], snap.output[1], snap.output[2], snap.output[3]
        );
        return;
    }

    // --------------------------------------------------------
    // ALL_STOP
    // --------------------------------------------------------
    if (strcasecmp(s, "ALL_STOP") == 0) {
        emergency_stop();
        send_ok("EMERGENCY STOP");
        return;
    }

    send_err("Unknown command");
}

// ============================================================
// UART 接收任务
// ============================================================
static void uart_task(void *arg)
{
    (void)arg;
    char cmd_buf[CMD_BUF_SIZE];
    int idx = 0;

    ESP_LOGI(TAG, "UART task started");

    while (1) {
        char c;
        int len = uart_read_bytes(UART_PORT, &c, 1, pdMS_TO_TICKS(20));
        if (len <= 0) {
            continue;
        }

        if (c == '\r' || c == '\n') {
            if (idx > 0) {
                cmd_buf[idx] = '\0';
                process_cmd(cmd_buf);
                idx = 0;
            }
        } else {
            if (idx < CMD_BUF_SIZE - 1) {
                cmd_buf[idx++] = c;
            } else {
                idx = 0;
                send_err("Command too long");
            }
        }
    }
}

// ============================================================
// 主入口
// ============================================================
void app_main(void)
{
    ESP_LOGI(TAG, "=== Coil Control System (PC-aligned, 10kHz) ===");

    // UART
    uart_config_t uart_cfg = {
        .baud_rate = SERIAL_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_param_config(UART_PORT, &uart_cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_PORT, UART_TX_PIN, UART_RX_PIN, -1, -1));
    ESP_ERROR_CHECK(uart_driver_install(UART_PORT, UART_BUF_SIZE, 0, 0, NULL, 0));
    ESP_LOGI(TAG, "UART initialized");

    // DAC
    dac_init();
    dac_zero_all();

    // 正弦查表
    init_sin_table();

    // 任务
    xTaskCreatePinnedToCore(wave_task, "wave", 4096, NULL, 5, &wave_task_handle, 1);
    xTaskCreatePinnedToCore(uart_task, "uart", 4096, NULL, 4, NULL, 0);

    // 定时器
    esp_timer_create_args_t timer_cfg = {
        .callback = timer_cb,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_ISR,
        .name = "wave_timer",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(esp_timer_create(&timer_cfg, &wave_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(wave_timer, WAVE_UPDATE_US));

    send_ok("System ready");
    ESP_LOGI(TAG, "System ready");
    ESP_LOGI(TAG, "Commands:");
    ESP_LOGI(TAG, "  VIBE:X,100,0.5");
    ESP_LOGI(TAG, "  VIBE_STOP");
    ESP_LOGI(TAG, "  SET_PHASE:0,90,OFF");
    ESP_LOGI(TAG, "  ROTATE:100,0.5");
    ESP_LOGI(TAG, "  ROTATE_STOP");
    ESP_LOGI(TAG, "  STATUS");
    ESP_LOGI(TAG, "  ALL_STOP");

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
