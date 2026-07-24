#!/bin/bash
# ============================================================
# DimOS 一键安装脚本 — Xubuntu / Ubuntu 22.04+
# 用途: 将 DimOS 克隆到项目目录内，安装依赖并验证 replay 功能
# 用法: chmod +x install_dimos_xubuntu.sh && ./install_dimos_xubuntu.sh
# 注意: dimos/ 目录已在 .gitignore 中排除，不会提交到仓库
# ============================================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

# 获取脚本所在目录（即项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIMOS_DIR="$SCRIPT_DIR/dimos"

echo "============================================"
echo "  DimOS 安装脚本 — Xubuntu / Ubuntu"
echo "  项目目录: $SCRIPT_DIR"
echo "  $(date)"
echo "============================================"
echo ""

# ---- Step 1: 系统依赖 ----
log "Step 1/5: 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    curl g++ portaudio19-dev git-lfs \
    libturbojpeg python3-dev python3-venv \
    libgl1 libegl1
log "系统依赖安装完成"

# ---- Step 2: 安装 uv ----
log "Step 2/5: 安装 uv 包管理器..."
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    log "uv 安装完成: $(uv --version)"
else
    log "uv 已安装: $(uv --version)"
fi

# ---- Step 3: 克隆 dimos 到项目目录 ----
log "Step 3/5: 克隆 DimOS 仓库到项目目录..."
if [ -d "$DIMOS_DIR" ]; then
    warn "目录 $DIMOS_DIR 已存在"
    read -p "是否重新克隆？(y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$DIMOS_DIR"
        log "已删除旧目录，重新克隆..."
    else
        log "保留现有目录，跳过克隆"
    fi
fi

if [ ! -d "$DIMOS_DIR" ]; then
    export GIT_LFS_SKIP_SMUDGE=1
    git clone https://github.com/dimensionalOS/dimos.git "$DIMOS_DIR"
    log "DimOS 仓库克隆完成 → $DIMOS_DIR"
fi

cd "$DIMOS_DIR"

# ---- Step 4: 创建 venv 并安装 ----
log "Step 4/5: 创建 Python 虚拟环境并安装 DimOS..."
log "（将下载约 3GB 依赖包，国内网络建议使用镜像，预计 10-30 分钟）"

if [ -d ".venv" ]; then
    warn "虚拟环境已存在，跳过创建"
else
    uv venv --python 3.12
fi

source .venv/bin/activate

# 如果在中国大陆，取消下面一行的注释以使用清华镜像加速:
# MIRROR_ARG="--index-url https://pypi.tuna.tsinghua.edu.cn/simple"

uv pip install --pre -e '.[base,unitree]' $MIRROR_ARG

log "DimOS 安装完成"

# ---- Step 5: 验证 ----
log "Step 5/5: 验证安装..."
echo ""

if dimos --help &> /dev/null; then
    log "dimos CLI 验证通过"
else
    err "dimos CLI 不可用，请检查安装日志"
    exit 1
fi

echo ""
echo "============================================"
echo "  安装完成！"
echo "============================================"
echo ""
echo "  项目结构:"
echo "    $SCRIPT_DIR"
echo "    ├── dimos/             ← DimOS 源码（已在 .gitignore 中排除）"
echo "    ├── robot-service/     ← 自定义模块"
echo "    ├── Roadmap/           ← 设计文档"
echo "    └── install_dimos_xubuntu.sh"
echo ""
echo "  可用命令:"
echo "    cd $SCRIPT_DIR/dimos && source .venv/bin/activate"
echo "    dimos --replay run unitree-go2    # 回放录制会话（无需硬件）"
echo "    dimos list                        # 列出所有可用蓝图"
echo "    dimos status                      # 查看运行状态"
echo "    dimos log -f                      # 跟踪日志"
echo "    dimos stop                        # 停止运行"
echo ""
echo "  DimOS 更新:"
echo "    cd $SCRIPT_DIR/dimos"
echo "    git pull"
echo "    source .venv/bin/activate"
echo "    uv pip install --pre -e '.[base,unitree]'"
echo ""
echo "============================================"