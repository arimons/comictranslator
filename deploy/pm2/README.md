# PM2 운영

코드와 `.venv`는 저장소에, 프로젝트·캐시·로그는 `~/ComicTranslator`에 둔다.
이미 PM2로 운영하는 맥 미니에서는 별도 앱용 launchd 데몬 대신 PM2에 `comictranslator`를 추가한다.

```sh
mkdir -p ~/ComicTranslator/logs
cd ~/Developer/NEW_COMIC_TRANSLATOR
pm2 start deploy/pm2/ecosystem.config.cjs --only comictranslator
pm2 save
pm2 status comictranslator
pm2 logs comictranslator --lines 50
pm2 restart comictranslator
```

`COMIC_TRANSLATOR_HOME`을 지정하면 데이터 홈을 변경할 수 있다. 기본 Python은 저장소의 `.venv/bin/python`이다. 가상환경을 다른 맥에서 복사하지 않고 대상 맥의 환경을 사용한다.
코드·패키지 갱신 후에는 이 앱만 재시작한다. 진행 중인 분석이 없을 때 재시작한다.

## 자동 시작

PM2의 저장 목록에 앱을 포함하고, 기존 PM2 launchd 시작 항목을 활성화한다. 앱이 비정상 종료되면 3초 뒤 재시작하며, 시작 직후 연속 실패가 10회를 넘으면 로그를 확인해야 한다.

현재 맥 미니는 자동 로그인 설정이며 FileVault가 꺼져 있다. 기존 `~/Library/LaunchAgents/pm2.changhyunpark.plist`의 `com.PM2` 항목을 `gui/501`에 등록했다. 이 항목은 `RunAtLoad: true`, `KeepAlive: false`인 일회성 로그인 작업으로 `pm2 resurrect`를 실행한다. 기존의 `LaunchOnlyOnce`·`OnDemand`는 제거하고 원본을 비공개 백업했다. 자동 로그인 설정을 바꾸면 로그인 전 실행을 보장하지 않는다.

```sh
launchctl print gui/$(id -u)/com.PM2
```

전체 PM2 종료/재시작은 다른 앱에도 영향을 주므로 만화 앱 운영에는 사용하지 않는다. `pm2 save`는 현재 전체 프로세스 목록을 저장한다.

## Tailscale HTTPS

앱은 `127.0.0.1:7873`에서만 수신한다. Tailscale Serve의 기존 HTTPS 443 서비스는 유지하고 8443을 이 앱에 연결한다. Funnel은 사용하지 않는다.

```sh
tailscale serve --bg --https=8443 http://127.0.0.1:7873
tailscale serve status
# 이 앱의 프록시만 해제할 때
tailscale serve --https=8443 off
```

macOS 앱 배포의 CLI는 `/Applications/Tailscale.app/Contents/MacOS/Tailscale`에 있을 수 있다.
접속은 `https://MAC_MINI_MAGIC_DNS_NAME:8443/`이며 접속 기기도 해당 tailnet에 연결돼 있어야 한다.
`--bg` 설정은 Tailscale이 재시작돼도 유지된다.

로그는 데이터 홈의 `logs/server.out.log`, `logs/server.err.log`에 저장한다. 기존 PM2 전체 운영 정책에 맞춰 로그 회전을 별도로 구성할 수 있다.

## 패키지

SSH를 통해 패키지 설치·업데이트·검증 모두 가능하다. 대상의 `.venv`를 명시해서 다른 서비스의 Python 환경과 분리한다. 현재 맥 미니 환경은 앱 import 및 LaMa CPU 추론을 통과해 이번 서비스 등록에 추가 설치는 필요하지 않았다. Python 3.11용 lock 파일을 다른 Python 버전에 그대로 적용하지 않는다.
