#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RDP + frp 部署脚本（离线版）
功能：开启远程桌面、配置防火墙、禁用睡眠/网卡节能、部署 frpc 并注册为系统服务
所有组件内嵌，无需联网下载
"""

import os
import sys
import json
import shutil
import subprocess
import ctypes
import time
import argparse
import socket
import re
from datetime import datetime

# ==================== 路径与配置常量 ====================

def get_script_dir():
    """获取脚本/exe 所在目录（兼容 PyInstaller 打包环境）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_script_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
FRPC_DIR = r"C:\frp"
FRPC_EXE = os.path.join(FRPC_DIR, "frpc.exe")
FRPC_CONFIG = os.path.join(FRPC_DIR, "frpc.toml")
SERVICE_NAME = "frpc_rdp"
APP_NAME = "RDP Tunnel Service"
LOG_FILE = os.path.join(FRPC_DIR, "deploy.log")
BACKUP_DIR = os.path.join(FRPC_DIR, "backup")
NSSM_EXE = os.path.join(FRPC_DIR, "nssm.exe")

# ==================== 资源提取（PyInstaller 内嵌文件） ====================

def get_resource_path(relative_path):
    """获取内嵌资源路径（兼容 PyInstaller 打包环境）"""
    if getattr(sys, '_MEIPASS', False):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(BASE_DIR, relative_path)

def extract_embedded_assets():
    """从 exe 内部提取 frpc.exe 和 nssm.exe 到 C:\frp"""
    log("提取内嵌组件...")
    os.makedirs(FRPC_DIR, exist_ok=True)

    frpc_src = get_resource_path("assets/frpc.exe")
    if os.path.exists(frpc_src):
        shutil.copy2(frpc_src, FRPC_EXE)
        log("frpc.exe 已提取: {}".format(FRPC_EXE))
    else:
        log("警告: 内嵌 frpc.exe 不存在", "WARN")

    nssm_src = get_resource_path("assets/nssm.exe")
    if os.path.exists(nssm_src):
        shutil.copy2(nssm_src, NSSM_EXE)
        log("nssm.exe 已提取: {}".format(NSSM_EXE))
    else:
        log("警告: 内嵌 nssm.exe 不存在", "WARN")

    return os.path.exists(FRPC_EXE) and os.path.exists(NSSM_EXE)

# ==================== 日志 ====================

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[{}] [{}] {}".format(ts, level, msg)
    try:
        os.makedirs(FRPC_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass
    if not getattr(sys, 'frozen', False):
        print(line)

# ==================== 权限与命令执行 ====================

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_cmd(cmd, check=True, timeout=60, shell=False):
    """执行命令，自动隐藏子进程窗口"""
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "shell": shell,
        "errors": "ignore"
    }
    if sys.platform == "win32" and not shell:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
    try:
        result = subprocess.run(cmd, **kwargs)
        if check and result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else ""
            log("命令失败: {} | {}".format(cmd_str, stderr), "ERROR")
            raise subprocess.CalledProcessError(result.returncode, cmd)
        return result
    except subprocess.TimeoutExpired:
        log("命令超时: {}".format(cmd_str), "ERROR")
        if check:
            raise
        return None
    except Exception as e:
        log("执行异常: {} | {}".format(cmd_str, e), "ERROR")
        if check:
            raise
        return None

# ==================== RDP 和系统配置 ====================

def enable_rdp():
    """强制开启远程桌面（允许连接 + NLA）并配置服务自启"""
    log("配置远程桌面注册表和服务...")
    os.makedirs(BACKUP_DIR, exist_ok=True)

    run_cmd([
        "reg", "export",
        r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server",
        os.path.join(BACKUP_DIR, "rdp_ts.reg"), "/y"
    ], check=False)

    run_cmd([
        "reg", "add", r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server",
        "/v", "fDenyTSConnections", "/t", "REG_DWORD", "/d", "0", "/f"
    ])
    run_cmd([
        "reg", "add", r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp",
        "/v", "UserAuthentication", "/t", "REG_DWORD", "/d", "1", "/f"
    ])

    run_cmd(["sc", "config", "TermService", "start=", "auto"])
    run_cmd(["sc", "config", "UmRdpService", "start=", "auto"])
    run_cmd(["sc", "start", "TermService"], check=False)
    run_cmd(["sc", "start", "UmRdpService"], check=False)
    log("远程桌面服务已配置为自动启动并尝试启动。")

def configure_firewall():
    """放行 RDP 端口 3389，兼容中英文系统"""
    log("配置防火墙规则...")

    run_cmd([
        "netsh", "advfirewall", "firewall", "set", "rule",
        'group="remote desktop"', "new", "enable=Yes"
    ], check=False)
    run_cmd([
        "netsh", "advfirewall", "firewall", "set", "rule",
        'group="远程桌面"', "new", "enable=Yes"
    ], check=False)

    ps_cmd = (
        'Get-NetFirewallRule -DisplayName "Remote Desktop*" | '
        'Where-Object {$_.Direction -eq "Inbound" -and $_.Protocol -eq "TCP" -and $_.LocalPort -eq 3389} | '
        'Enable-NetFirewallRule'
    )
    run_cmd(["powershell", "-Command", ps_cmd], check=False, timeout=15)

    check = run_cmd([
        "netsh", "advfirewall", "firewall", "show", "rule", "name=RDP-3389-TCP-In"
    ], check=False)
    if check and ("找不到" in check.stdout or check.returncode != 0):
        run_cmd([
            "netsh", "advfirewall", "firewall", "add", "rule",
            "name=RDP-3389-TCP-In", "dir=in", "protocol=TCP",
            "localport=3389", "action=allow", "profile=any"
        ], check=False)
        log("已添加自定义 TCP 3389 入站规则。")

def add_rdp_user(username=None):
    """将当前用户或指定用户加入 Remote Desktop Users 组"""
    target = username or os.environ.get("USERNAME")
    if not target:
        log("无法获取用户名，跳过加入远程桌面用户组。", "WARN")
        return
    res = run_cmd(["net", "localgroup", "Remote Desktop Users", target, "/add"], check=False)
    if res and res.returncode == 0:
        log("用户 [{}] 已加入远程桌面用户组。".format(target))
    else:
        log("用户 [{}] 可能已在组中，或添加失败（非致命）。".format(target), "WARN")

# ==================== 电源与网卡节能控制 ====================

def set_power_plan():
    """关闭睡眠、休眠，将所有超时归零，并激活高性能电源方案"""
    log("配置电源选项...")
    run_cmd(["powercfg", "-h", "off"], check=False)

    res = run_cmd(["powercfg", "/getactivescheme"], check=False)
    if res and res.stdout:
        match = re.search(r"([\w\d-]{36})", res.stdout)
        if match:
            old_guid = match.group(1)
            backup_file = os.path.join(BACKUP_DIR, "power_plan.txt")
            try:
                f = open(backup_file, "w", encoding="utf-8")
                f.write(old_guid)
                f.close()
            except Exception as e:
                log("备份电源方案失败: {}".format(e), "WARN")

    res = run_cmd(["powercfg", "/list"], check=False)
    high_perf_guid = None
    if res and res.stdout:
        for line in res.stdout.splitlines():
            if "高性能" in line or "High performance" in line:
                match = re.search(r"([\w\d-]{36})", line)
                if match:
                    high_perf_guid = match.group(1)
                    break

    if high_perf_guid:
        run_cmd(["powercfg", "-setactive", high_perf_guid], check=False)
        log("已激活高性能电源方案 {}".format(high_perf_guid))
    else:
        log("未找到高性能方案，使用当前电源方案并将所有超时设为 0。", "WARN")

    policies = [
        ("-standby-timeout-ac", "0"), ("-standby-timeout-dc", "0"),
        ("-disk-timeout-ac", "0"),    ("-disk-timeout-dc", "0"),
        ("-monitor-timeout-ac", "0"),  ("-monitor-timeout-dc", "0"),
        ("-hibernate-timeout-ac", "0"), ("-hibernate-timeout-dc", "0")
    ]
    for param, val in policies:
        run_cmd(["powercfg", "-change", param, val], check=False)

    log("电源超时策略已全部设为 0。")

def disable_nic_powersaving():
    """禁用有线/无线网卡的节能模式"""
    log("禁用网卡节能...")
    run_cmd([
        "powershell", "-Command",
        'Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq "802.3" -or $_.PhysicalMediaType -eq "Native 802.11"} | Disable-NetAdapterPowerManagement'
    ], check=False, timeout=20)

    try:
        res = run_cmd(["powercfg", "/devicequery", "wake_from_any"], check=False)
        if res and res.stdout:
            for dev in res.stdout.splitlines():
                dev_clean = dev.strip()
                if dev_clean:
                    run_cmd(["powercfg", "/devicedisablewake", dev_clean], check=False)
    except Exception as e:
        log("清除设备唤醒权限时出错: {}".format(e), "WARN")

    log("网卡节能禁用流程执行完毕。")

def enable_nic_powersaving():
    """恢复网卡节能（卸载用）"""
    log("恢复网卡节能...")
    run_cmd([
        "powershell", "-Command",
        'Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq "802.3" -or $_.PhysicalMediaType -eq "Native 802.11"} | Enable-NetAdapterPowerManagement'
    ], check=False, timeout=20)
    log("网卡节能恢复流程执行完毕。")

# ==================== frp 配置 ====================

def deploy_frp_config(cfg):
    """写入 frpc.toml 配置文件"""
    log("写入 frpc 配置文件...")

    server_addr = cfg['server_addr']
    server_port = cfg['server_port']
    token = cfg['token']
    remote_port = cfg['remote_port']

    toml_lines = [
        'serverAddr = "{}"'.format(server_addr),
        'serverPort = {}'.format(server_port),
        'auth.method = "token"',
        'auth.token = "{}"'.format(token),
        '',
        'transport.poolCount = 8',
        'transport.tcpMux = true',
        'transport.tcpMuxKeepaliveInterval = 30',
        '',
        'log.to = "console"',
        '',
        '[[proxies]]',
        'name = "rdp_tunnel"',
        'type = "tcp"',
        'localIP = "127.0.0.1"',
        'localPort = 3389',
        'remotePort = {}'.format(remote_port),
        ''
    ]

    with open(FRPC_CONFIG, "w", encoding="utf-8") as f:
        f.write("\n".join(toml_lines))

    run_cmd(["icacls", FRPC_CONFIG, "/inheritance:r"], check=False)
    run_cmd(["icacls", FRPC_CONFIG, "/grant", "SYSTEM:(R,W)"], check=False)
    run_cmd(["icacls", FRPC_CONFIG, "/grant", "Administrators:(R,W)"], check=False)
    log("配置文件权限已收紧（SYSTEM + Administrators）。")

def test_frp_server(addr, port, timeout=5):
    """测试 frp 服务器端口是否可达"""
    try:
        sock = socket.create_connection((addr, port), timeout=timeout)
        sock.close()
        return True
    except Exception as e:
        log("frp 服务器连通性测试失败: {}".format(e), "WARN")
        return False

# ==================== 系统服务注册 ====================

def get_service_status():
    """查询服务状态"""
    res = run_cmd(["sc", "query", SERVICE_NAME], check=False)
    if res and res.returncode == 0:
        if "RUNNING" in res.stdout:
            return "RUNNING"
        elif "STOPPED" in res.stdout:
            return "STOPPED"
        elif "START_PENDING" in res.stdout:
            return "START_PENDING"
    return "NOT_FOUND"

def register_frp_service():
    """使用 NSSM 将 frpc 注册为延迟启动的 Windows 服务"""
    log("注册 frpc 系统服务...")

    current = get_service_status()
    if current != "NOT_FOUND":
        run_cmd([NSSM_EXE, "stop", SERVICE_NAME], check=False, timeout=15)
        time.sleep(1)
        run_cmd([NSSM_EXE, "remove", SERVICE_NAME, "confirm"], check=False, timeout=15)
        time.sleep(1)

    logs_dir = os.path.join(FRPC_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    run_cmd([NSSM_EXE, "install", SERVICE_NAME, FRPC_EXE])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppDirectory", FRPC_DIR])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppParameters", '-c "{}"'.format(FRPC_CONFIG)])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "DisplayName", APP_NAME])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "Description", "frpc remote desktop tunnel service (auto-deployed)"])

    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "Start", "SERVICE_DELAYED_AUTO_START"])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppExit", "Default", "Restart"])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppRestartDelay", "10000"])

    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppStdout", os.path.join(logs_dir, "frpc_access.log")])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppStderr", os.path.join(logs_dir, "frpc_errors.log")])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppStdoutCreationDisposition", "4"])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppStderrCreationDisposition", "4"])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppRotateFiles", "1"])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppRotateBytes", "10485760"])
    run_cmd([NSSM_EXE, "set", SERVICE_NAME, "AppRotateKeep", "5"])

    run_cmd([NSSM_EXE, "start", SERVICE_NAME], check=False)
    time.sleep(3)

    status = get_service_status()
    if status == "RUNNING":
        log("frpc 服务已成功启动并处于运行状态。")
    elif status == "START_PENDING":
        log("frpc 服务正在启动中...", "INFO")
    else:
        log("frpc 服务已注册为延迟启动，将在系统网络就绪后自动运行。", "INFO")

# ==================== 卸载 ====================

def uninstall_all():
    """停止并移除服务，恢复部分系统设置，删除程序目录"""
    log("开始卸载...")

    if os.path.exists(NSSM_EXE):
        run_cmd([NSSM_EXE, "stop", SERVICE_NAME], check=False, timeout=15)
        time.sleep(1)
        run_cmd([NSSM_EXE, "remove", SERVICE_NAME, "confirm"], check=False, timeout=15)
        time.sleep(1)
    else:
        run_cmd(["sc", "stop", SERVICE_NAME], check=False, timeout=15)
        run_cmd(["sc", "delete", SERVICE_NAME], check=False, timeout=15)

    run_cmd([
        "reg", "add", r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server",
        "/v", "fDenyTSConnections", "/t", "REG_DWORD", "/d", "1", "/f"
    ], check=False)
    log("已将远程桌面连接恢复为默认禁用状态。")

    run_cmd([
        "netsh", "advfirewall", "firewall", "set", "rule",
        'group="remote desktop"', "new", "enable=No"
    ], check=False)
    run_cmd([
        "netsh", "advfirewall", "firewall", "set", "rule",
        'group="远程桌面"', "new", "enable=No"
    ], check=False)
    run_cmd([
        "netsh", "advfirewall", "firewall", "delete", "rule", "name=RDP-3389-TCP-In"
    ], check=False)
    log("防火墙规则已恢复。")

    pb_file = os.path.join(BACKUP_DIR, "power_plan.txt")
    if os.path.exists(pb_file):
        try:
            f = open(pb_file, "r", encoding="utf-8")
            old_guid = f.read().strip()
            f.close()
            if old_guid:
                run_cmd(["powercfg", "-setactive", old_guid], check=False)
                log("已恢复原始电源方案 {}".format(old_guid))
        except Exception as e:
            log("恢复电源方案失败: {}".format(e), "WARN")

    enable_nic_powersaving()

    if os.path.exists(FRPC_DIR):
        for _ in range(3):
            try:
                shutil.rmtree(FRPC_DIR, ignore_errors=True)
                if not os.path.exists(FRPC_DIR):
                    break
                time.sleep(1)
            except Exception as e:
                log("删除目录重试中: {}".format(e), "WARN")
        if os.path.exists(FRPC_DIR):
            log("无法完全删除 {}，请手动清理。".format(FRPC_DIR), "WARN")
        else:
            log("已删除目录 {}".format(FRPC_DIR))

    log("卸载完成。")

# ==================== 配置加载 ====================

def create_config_template():
    """在脚本/exe 同目录生成配置文件模板"""
    sample = {
        "server_addr": "your.frp.server.com",
        "server_port": 7000,
        "token": "your_token_here",
        "remote_port": 6000
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(sample, f, indent=4, ensure_ascii=False)
    log("未找到配置文件，已生成模板: {}".format(CONFIG_FILE), "WARN")

def load_config(args):
    """优先从命令行参数获取配置，否则从 config.json 读取"""
    if args.server:
        return {
            "server_addr": args.server,
            "server_port": args.port,
            "token": args.token,
            "remote_port": args.remote_port
        }

    if not os.path.exists(CONFIG_FILE):
        create_config_template()
        sys.exit(0)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        log("读取配置文件失败: {}".format(e), "ERROR")
        create_config_template()
        sys.exit(1)

    if not cfg.get("server_addr") or cfg.get("server_addr") in ("x.x.x.x", "your.frp.server.com", ""):
        log("配置文件中的 server_addr 无效，请修改后再试。", "ERROR")
        sys.exit(1)
    if not cfg.get("token") or cfg.get("token") in ("your_token_here", ""):
        log("配置文件中的 token 无效，请修改后再试。", "ERROR")
        sys.exit(1)

    return cfg

# ==================== 主流程 ====================

def main():
    parser = argparse.ArgumentParser(description="RDP + frp 远程桌面穿透部署工具（离线版）")
    parser.add_argument("--uninstall", action="store_true", help="卸载服务并清理环境")
    parser.add_argument("--server", help="frp 服务器地址")
    parser.add_argument("--port", type=int, default=7000, help="frp 服务器端口")
    parser.add_argument("--token", help="frp 认证 token")
    parser.add_argument("--remote-port", type=int, default=6000, help="穿透后的公网端口")
    parser.add_argument("--username", help="指定加入远程桌面用户组的用户名")
    args = parser.parse_args()

    if not is_admin():
        log("需要管理员权限，请右键以管理员身份运行。", "ERROR")
        sys.exit(1)

    if args.uninstall:
        uninstall_all()
        return

    log("=== RDP + frp 远程桌面穿透部署开始（离线版）===")

    if not extract_embedded_assets():
        log("核心组件提取失败，请检查程序完整性。", "ERROR")
        sys.exit(1)

    cfg = load_config(args)

    if not test_frp_server(cfg["server_addr"], cfg["server_port"]):
        log("警告：无法连接到 frp 服务器，请检查网络或配置。", "WARN")

    enable_rdp()
    add_rdp_user(args.username)
    configure_firewall()
    set_power_plan()
    disable_nic_powersaving()
    deploy_frp_config(cfg)
    register_frp_service()

    log("部署完成。远程桌面端口 {} 已映射到本机 3389 端口。".format(cfg['remote_port']))
    log("系统将在重启后保持远程桌面与穿透服务持续在线。")
    log("日志文件: {}".format(LOG_FILE))

if __name__ == "__main__":
    main()
