# Proxmox LXC Allocator

> [!WARNING]
> **Internal networks only / 내부망 전용**
>
> Do not expose this application directly to the public internet. 인터넷에 직접 공개하지 마세요.

[한국어](#한국어) · [English](#english)

## 한국어

### 소개

기존 Proxmox LXC 컨테이너를 선착순으로 할당하는 소규모 셀프서비스 웹 앱입니다. Proxmox 사용자를 만들고, 설정한 VMID 범위에서 아직 할당되지 않은 첫 번째 컨테이너의 권한을 부여합니다.

영어와 한국어 UI를 하나의 코드베이스에서 지원합니다. 이 앱은 컨테이너를 생성하거나 초기화하지 않으므로, 사용할 LXC를 미리 준비해야 합니다.

이 프로젝트는 실험적 소프트웨어이며 **방화벽으로 제한된 신뢰 가능한 내부망에서만 사용하도록 설계되었습니다.** 할당 화면에는 별도 사용자 인증이 없으므로 공개 인터넷에 노출하면 누구나 계정을 만들고 컨테이너를 소진할 수 있습니다.

### 설치

Linux와 Python 3가 필요합니다.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

`.env`를 수정하고 `APP_SECRET_KEY`와 `ADMIN_PASSWORD`에 서로 다른 값을 사용하세요. 다음 명령을 각각 실행해 안전한 값을 만들 수 있습니다.

```sh
python -c 'import secrets; print(secrets.token_hex(32))'
```

설정을 불러온 뒤 실행합니다.

```sh
set -a
. ./.env
set +a
gunicorn --workers 2 --bind 127.0.0.1:5000 app:app
```

`/`는 할당 화면이고 `/admin`은 할당 현황과 CSV 다운로드 화면입니다. `APP_LANGUAGE=ko`는 한국어, `APP_LANGUAGE=en`은 영어 UI를 사용합니다.

### Proxmox 준비

- root 토큰 대신 이 앱만을 위한 API 사용자와 토큰을 만드세요.
- 사용자 생성 및 설정된 VMID에 `LXC_ROLE`을 부여하는 데 필요한 최소 권한만 주세요.
- `LXC_START_VMID`부터 `LXC_END_VMID`까지 실제 LXC가 존재하며 다른 사용자에게 할당되지 않았는지 확인하세요.
- TLS 인증서 검증은 기본으로 켜져 있습니다. 사설 CA는 `REQUESTS_CA_BUNDLE`로 지정하세요. `PROXMOX_VERIFY_SSL=false`는 격리된 신뢰망에서도 최후의 수단으로만 사용하세요.
- 기본 역할은 `PVEVMUser`입니다. 가능하면 필요한 권한만 가진 더 좁은 사용자 정의 역할을 사용하세요.
- 운영 전에 폐기 가능한 테스트 LXC로 사용자 생성, ACL 부여, 로그인, 비밀번호 변경을 직접 확인하세요.

### 운영 및 상태 파일

- 방화벽이나 VPN으로 접근 가능한 내부망을 제한하고, 내부망에서도 HTTPS 리버스 프록시를 사용하세요.
- 할당 화면은 별도 비밀번호 없이 사용자 이름만 받습니다. 방화벽이나 VPN에서 허가된 사용자만 앱에 접속할 수 있게 제한하세요.
- `issued_log.csv`는 단순 로그가 아니라 할당 상태입니다. 활성 VMID 범위에서 삭제하거나 임의로 수정하면 중복 할당될 수 있습니다.
- API 작업 전에 VMID를 `pending`으로 예약합니다. 중간 종료나 불확실한 실패는 `pending` 또는 `needs_review`로 남아 재할당을 차단합니다.
- 이런 항목은 Proxmox의 사용자와 ACL 상태를 직접 확인한 뒤, 서비스를 중지한 상태에서만 CSV를 수정하세요.
- 상태 파일에는 사용자 이름, VMID, UTC 시각, 상태가 저장됩니다. 사용자 이름도 개인정보가 될 수 있으므로 보호하고 백업하세요.
- `.env`, CSV, 잠금 파일은 Git에 올리지 마세요. 기본 파일 패턴은 `.gitignore`에 포함되어 있습니다.
- 보안 세션 쿠키가 기본 활성화됩니다. 로컬 HTTP 시험에서만 `SESSION_COOKIE_SECURE=false`를 사용하세요.

## English

### Overview

This is a small self-service web application that assigns prepared Proxmox LXC containers on a first-come basis. It creates a Proxmox user and grants that user access to the first unassigned container in a configured VMID range.

One codebase supports English and Korean. The application does not create or initialize containers; prepare every LXC before enabling allocation.

This project is experimental and is **designed only for a trusted internal network protected by a firewall.** The allocation page has no end-user authentication; exposing it to the public internet lets anyone create accounts and exhaust the container pool.

### Installation

Linux and Python 3 are required.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env` and use different values for `APP_SECRET_KEY` and `ADMIN_PASSWORD`. Run this command separately for each secret:

```sh
python -c 'import secrets; print(secrets.token_hex(32))'
```

Load the configuration and start the service:

```sh
set -a
. ./.env
set +a
gunicorn --workers 2 --bind 127.0.0.1:5000 app:app
```

`/` is the allocation page and `/admin` shows and downloads allocation records. Use `APP_LANGUAGE=ko` for Korean or `APP_LANGUAGE=en` for English.

### Proxmox preparation

- Create a dedicated API user and token instead of using a root token.
- Grant only the permissions required to create users and assign `LXC_ROLE` on the configured VMIDs.
- Confirm that every LXC from `LXC_START_VMID` through `LXC_END_VMID` exists and is not already assigned to another user.
- TLS certificate verification is enabled by default. Set `REQUESTS_CA_BUNDLE` for a private CA. Use `PROXMOX_VERIFY_SSL=false` only as a last resort on an isolated trusted network.
- The default role is `PVEVMUser`. Prefer a narrower custom role when possible.
- Before production use, test user creation, ACL assignment, sign-in, and password changes with a disposable LXC.

### Operation and state

- Restrict access with a firewall or VPN and use an HTTPS reverse proxy even on the internal network.
- The allocation page asks only for a username. Restrict the app at the firewall or VPN so only authorized users can reach it.
- `issued_log.csv` is allocation state, not a disposable log. Deleting or editing it for an active VMID range can assign a container twice.
- The app reserves a VMID as `pending` before changing Proxmox. An interrupted or uncertain operation remains `pending` or `needs_review`, preventing reassignment.
- Verify the corresponding Proxmox user and ACL manually before editing such an entry, and stop the service before changing the CSV.
- The state file contains the username, VMID, UTC timestamp, and status. Treat usernames as potentially personal data and protect and back up the file.
- Do not commit `.env`, CSV files, or lock files. The default patterns are included in `.gitignore`.
- Secure session cookies are enabled by default. Use `SESSION_COOKIE_SECURE=false` only for local HTTP testing.

## License

이 프로젝트는 GNU Affero General Public License v3.0 이상(`AGPL-3.0-or-later`)으로 배포됩니다.

This project is licensed under the GNU Affero General Public License v3.0 or later (`AGPL-3.0-or-later`). See [LICENSE](LICENSE).
