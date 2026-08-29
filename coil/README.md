# ESP-IDF 版本 — 可控磁场系统固件

## 在 VS Code (ESP-IDF 扩展) 中打开

1. VS Code → **Ctrl+Shift+P** → `ESP-IDF: Open Project`
2. 选择 `firmware_esp_idf/` 文件夹
3. 按 **F1** → `ESP-IDF: Set ESP-IDF Target` → **esp32s3**
4. 按 **F1** → `ESP-IDF: Build Project`

## 命令行编译烧录

```bash
cd firmware_esp_idf
idf.py set-target esp32s3
idf.py build
idf.py -p COM7 flash monitor
```

## 串口协议

同 Arduino 版本，参见 `docs/protocol.md`
