"""看板配置：Python 文件会随 Studio Agent 构建打包。

HOST 为空关闭发送；当前地址为本机 Docker Desktop 的 Mac 宿主机地址。
迁移到其他机器/远程环境时，改为 Agent 可访问的看板电脑 IPv4。
"""
HOST = "192.168.65.254"
PORT = 9871
HZ = 30
