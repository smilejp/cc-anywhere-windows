# CC-Anywhere Windows

Windows용 Claude Code 원격 접속 시스템. WezTerm을 백엔드로 사용하여 WSL 없이 네이티브 Windows에서 실행됩니다.

> **Note**: macOS/Linux 사용자는 [cc-anywhere](https://github.com/keecon/cc-anywhere) (tmux 기반) 버전을 사용하세요.

## 요구 사항

- Windows 10/11
- Python 3.11+
- [WezTerm](https://wezfurlong.org/wezterm/) 터미널
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) (npm으로 설치)

## 빠른 시작

### 1. 필수 프로그램 설치

```powershell
# WezTerm 설치
winget install wez.wezterm

# Claude CLI 설치 (Node.js 필요)
npm install -g @anthropic-ai/claude-code

# Python 3.11+ 확인
python --version
```

### 2. cc-anywhere-windows 설치

```powershell
# 저장소 클론
git clone https://github.com/smilejp/cc-anywhere-windows.git
cd cc-anywhere-windows

# 패키지 설치
pip install -e ".[dev]"
```

### 3. 서버 실행

```powershell
# 서버 시작
python -m cc_anywhere

# 브라우저에서 열기
# http://localhost:8080
```

## 상세 설치 가이드

### WezTerm 설치

```powershell
# winget으로 설치 (권장)
winget install wez.wezterm

# 또는 Chocolatey로 설치
choco install wezterm

# 또는 Scoop으로 설치
scoop bucket add extras
scoop install wezterm
```

설치 후 **새 터미널을 열고** PATH에 있는지 확인:

```powershell
wezterm --version
```

> ⚠️ **중요**: WezTerm 설치 후 터미널을 재시작해야 PATH가 갱신됩니다.

### Claude CLI 설치

```powershell
# Node.js가 없다면 먼저 설치
winget install OpenJS.NodeJS

# Claude CLI 설치
npm install -g @anthropic-ai/claude-code

# 설치 확인
claude --version
```

### Python 환경 설정 (선택사항)

가상 환경 사용을 권장합니다:

```powershell
# 가상 환경 생성
python -m venv .venv

# 가상 환경 활성화
.\.venv\Scripts\Activate.ps1

# 패키지 설치
pip install -e ".[dev]"
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

## Windows 관련 주의사항

### PATH 환경변수

WezTerm과 Claude CLI가 PATH에 포함되어 있어야 합니다:

```powershell
# PATH 확인
$env:PATH -split ';' | Select-String -Pattern 'wezterm|nodejs|npm'

# 수동으로 PATH 추가 (필요시)
# WezTerm: 보통 C:\Program Files\WezTerm 또는 사용자 폴더
# Node.js: 보통 C:\Program Files\nodejs
```

### PowerShell 실행 정책

스크립트 실행이 차단될 경우:

```powershell
# 현재 정책 확인
Get-ExecutionPolicy

# 정책 변경 (관리자 권한 필요)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 방화벽 설정

외부 접속을 허용하려면 Windows 방화벽에서 포트를 열어야 합니다:

```powershell
# 인바운드 규칙 추가 (관리자 권한 필요)
New-NetFirewallRule -DisplayName "CC-Anywhere" -Direction Inbound -Port 8080 -Protocol TCP -Action Allow
```

### 홈 디렉토리 경로

`~`는 `%USERPROFILE%` (예: `C:\Users\사용자명`)으로 확장됩니다.
config.yaml에서 작업 디렉토리를 명시적으로 지정할 수 있습니다:

```yaml
claude:
  default_working_dir: "D:\\Projects"  # 백슬래시는 이스케이프 필요
  # 또는
  default_working_dir: "D:/Projects"   # 슬래시도 동작함
```

## 문제 해결 (Troubleshooting)

### "WezTerm not found" 에러

```
WezTermNotFoundError: WezTerm not found. Please install WezTerm: winget install wez.wezterm
```

**해결방법:**
1. WezTerm이 설치되어 있는지 확인
2. 터미널을 재시작하여 PATH 갱신
3. `wezterm --version`으로 확인

### "claude" 명령을 찾을 수 없음

**해결방법:**
1. Claude CLI 설치: `npm install -g @anthropic-ai/claude-code`
2. Node.js가 PATH에 있는지 확인
3. 터미널 재시작

### 세션 생성 실패

WezTerm pane 생성이 실패하는 경우:

1. WezTerm이 실행 중인지 확인 (서버가 WezTerm 창을 자동 생성함)
2. 작업 디렉토리가 존재하는지 확인
3. 로그 확인: `config/config.yaml`에서 `logging.level: DEBUG` 설정

### WebSocket 연결 끊김

브라우저에서 터미널 연결이 자주 끊기는 경우:

1. 방화벽/보안 소프트웨어 확인
2. HTTPS 사용 고려: `python -m cc_anywhere ssl setup`

### Discord 봇 연결 안됨

1. `.env` 파일에 `DISCORD_BOT_TOKEN` 설정 확인
2. `config/config.yaml`에서 `discord.enabled: true` 확인
3. 봇에 필요한 권한(Intents) 활성화 확인

## 제한 사항

1. **Windows 전용** - macOS/Linux에서는 동작하지 않습니다 (tmux 기반 cc-anywhere 사용)
2. **WezTerm 필수** - 사전에 WezTerm 설치가 필요합니다
3. **Pane 크기 조절** - WezTerm CLI는 pane 크기 조절을 완전히 지원하지 않음
4. **시그널 처리** - Windows에서 Ctrl+C 처리가 Linux/macOS와 다르게 동작할 수 있음

## 알려진 이슈

- [ ] Windows Terminal 대신 WezTerm 사용 필요 (Windows Terminal은 CLI 제어 미지원)
- [ ] 긴 경로명(260자 초과)에서 문제 발생 가능 - Windows Long Path 활성화 권장
- [ ] 한글 경로에서 인코딩 문제 가능성 - 영문 경로 사용 권장

## 라이선스

MIT License
