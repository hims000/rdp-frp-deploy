# RDP + frp 远程桌面穿透部署工具（离线版）

## 功能
- 开启 Windows 远程桌面（RDP）
- 配置防火墙放行 3389 端口
- 禁用睡眠/休眠/网卡节能
- 部署 frpc 内网穿透客户端
- 注册为 Windows 系统服务（延迟启动 + 自动重启）

## 使用方式

1. 下载 `rdp_frp_deploy.exe`
2. 将 exe 放入目标文件夹
3. **首次运行**（管理员身份）：自动生成 `config.json` 模板
4. 编辑 `config.json`，填写 frp 服务器信息
5. **再次运行**（管理员身份）：完成部署

## 配置文件示例

```json
{
    "server_addr": "your.frp.server.com",
    "server_port": 7000,
    "token": "your_secret_token",
    "remote_port": 6000
}
