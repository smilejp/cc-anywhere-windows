# CC-Anywhere Windows

Windows용 Claude Code 원격 접속 시스템. WezTerm을 백엔드로 사용하여 WSL 없이 네이티브 Windows에서 실행됩니다.

> **Note**: macOS/Linux 사용자는 [cc-anywhere](https://github.com/keecon/cc-anywhere) (tmux 기반) 버전을 사용하세요.

## 요구 사항

- Windows 10/11
- Python 3.11+
- [WezTerm](https://wezfurlong.org/wezterm/) 터미널

## WezTerm 설치

```powershell
# winget으로 설치 (권장)
winget install wez.wezterm

# 또는 Chocolatey로 설치
choco install wezterm
```

설치 후 `wezterm` 명령이 PATH에 있는지 확인하세요:

```powershell
wezterm --version
```

## 설치

```powershell
# 저장소 클론
git clone https://github.com/keecon/cc-anywhere-windows.git
cd cc-anywhere-windows

# 개발 모드로 설치
pip install -e ".[dev]"
```

## 실행

```powershell
# 서버 시작
python -m cc_anywhere

# 브라우저에서 열기
# http://localhost:8080
```

## 구성

`config/config.yaml` 파일에서 설정을 변경할 수 있습니다:

```yaml
server:
  host: "0.0.0.0"
  port: 8080

claude:
  command: "claude"
  args: ["--dangerously-skip-permissions"]
  default_working_dir: "~"

sessions:
  max_sessions: 10
```

## 기능

- **웹 터미널**: 브라우저에서 Claude Code 세션 관리
- **세션 관리**: 다중 세션 생성/삭제/전환
- **실시간 출력**: WebSocket을 통한 실시간 터미널 출력
- **Discord 봇**: Discord에서 세션 제어 (선택사항)
- **모바일 지원**: 반응형 UI로 모바일에서도 사용 가능

## 아키텍처

```
cc-anywhere-windows
├── WezTerm CLI       # 터미널 백엔드 (tmux 대신)
├── FastAPI           # 웹 서버
├── WebSocket         # 실시간 통신
└── xterm.js          # 웹 터미널 UI
```

### tmux vs WezTerm

| 기능 | cc-anywhere (tmux) | cc-anywhere-windows (WezTerm) |
|------|-------------------|------------------------------|
| 플랫폼 | macOS, Linux | Windows |
| 터미널 | tmux | WezTerm |
| 세션 식별 | tmux session name | WezTerm pane ID |
| 설치 요구 | tmux | WezTerm |

## CLI 명령

```powershell
# 도움말
python -m cc_anywhere --help

# 서버 시작
python -m cc_anywhere

# Hook 관리
python -m cc_anywhere hooks install
python -m cc_anywhere hooks status
python -m cc_anywhere hooks uninstall

# SSL 인증서 (HTTPS용)
python -m cc_anywhere ssl setup
python -m cc_anywhere ssl status
```

## 외부 접속 (Tailscale)

원격에서 접속하려면 Tailscale을 사용하세요:

1. [Tailscale](https://tailscale.com/) 설치 및 로그인
2. 서버 시작: `python -m cc_anywhere`
3. 다른 기기에서: `http://<tailscale-ip>:8080`

## 제한 사항

1. **Windows 전용** - macOS/Linux에서는 동작하지 않습니다
2. **WezTerm 필수** - 사전에 WezTerm 설치가 필요합니다
3. **Pane 관리** - tmux와 달리 WezTerm의 workspace/pane 구조 사용

## 라이선스

MIT License
