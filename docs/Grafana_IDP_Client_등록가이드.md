# Grafana IDP Client 등록 가이드 (mwm-idp 전용)

본 문서는 Grafana(Phase 06 Observability 스택)를 `mwm-idp`의 OIDC 클라이언트로 등록하여 SSO를 활성화하기 위한 운영용 가이드입니다. 개발 환경에서 이미 검증된 구성을 그대로 production에 옮길 때 참조합니다.

> 참고: `mwm-idp` 소스 위치는 `/home/hennry/GitHub/mw/mw_app/idp` 입니다. IdP 자체 설치/운영은 해당 저장소 README를 따릅니다.

---

## 1. 사전 조건

| 항목 | 요건 |
| :--- | :--- |
| `mwm-idp` 가동 | OIDC Discovery (`/.well-known/openid-configuration`) 정상 응답 |
| `mwm-idp` PKCE | **미지원** — Grafana 측 `use_pkce = false` 필수 |
| Grafana 외부 접근 URL | 사용자 브라우저가 실제로 사용하는 FQDN (Grafana `root_url`과 정확히 일치) |
| TLS | IdP / Grafana 모두 HTTPS. self-signed 사용 시 Grafana에 `tls_skip_verify_insecure = true` 필요. 정식 CA 사용 권장 (production) |
| Grafana 컨테이너에서 IdP 도달 | `extra_hosts` 또는 정상 DNS, 방화벽 22/443/20443 등 통신 가능 |

---

## 2. 클라이언트 등록 정보

IdP Admin Console (`https://<IDP_HOST>/clients/add`) 또는 REST API로 등록합니다.

| 항목 | 값 (예시) | 비고 |
| :--- | :--- | :--- |
| **Client ID** | `grafana-client` | 식별자. 운영 환경에서는 `grafana-prod` 등으로 분리 권장 |
| **Client Secret** | 32자 이상 무작위 문자열 | `.env`에만 저장. 환경별로 다르게 발급 |
| **Client Name** | `Grafana` | 표시용 |
| **Redirect URIs** | `https://<GRAFANA_HOST>/grafana/login/generic_oauth` | **공백 구분 + 정확 일치** 검증. sub-path 사용 시 `/grafana` 포함 필수 |
| **Grant Types** | `authorization_code refresh_token` | 둘 다 등록 |
| **Scope** | `openid profile email groups` | `groups` 누락 시 역할 매핑 불가 |
| **Policy Mapping** | `{}` | Grafana는 IdP `roles`/`groups`를 그대로 사용 (별도 변환 불필요). MinIO 등 정책이 다른 클라이언트만 매핑 채움 |

> Redirect URI 주의: `mwm-idp`는 `models.py`의 `check_redirect_uri()`에서 **공백 split + 완전 문자열 일치**로 검증합니다. 슬래시 1개만 달라도 거부됩니다. Grafana sub-path 변경 시 반드시 동기화.

### 2-1. UI 등록

1. `https://<IDP_HOST>/login` → Admin/PowerUser 계정 로그인
2. 좌측 메뉴 → **Clients** → **Add Client**
3. 위 표 값 입력 → 저장

### 2-2. REST API 등록 (자동화)

```bash
curl -k -X POST https://<IDP_HOST>/api/clients \
  -H "Authorization: Bearer <mwm_sk_관리자키>" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "grafana-client",
    "client_secret": "<32자+ 랜덤>",
    "client_name": "Grafana",
    "redirect_uris": "https://<GRAFANA_HOST>/grafana/login/generic_oauth",
    "grant_types": "authorization_code refresh_token",
    "scope": "openid profile email groups",
    "policy_mapping": {}
  }'
```

성공 시 201, `client_id` 중복이면 409.

---

## 3. Grafana 측 설정

### 3-1. 환경 변수 (`.env`)

```ini
# --- Grafana ↔ mwm-idp OIDC SSO ---
GRAFANA_ROOT_URL=https://<GRAFANA_HOST>/grafana
GRAFANA_ADMIN_PASSWORD=<강력한 비상용 비밀번호>

GRAFANA_OIDC_CLIENT_ID=grafana-client
GRAFANA_OIDC_CLIENT_SECRET=<2-1에서 발급받은 secret>
GRAFANA_OIDC_AUTH_URL=https://<IDP_HOST>/oauth/authorize
GRAFANA_OIDC_TOKEN_URL=https://<IDP_HOST>/oauth/token
# 주의: mwm-idp의 userinfo는 /api/userinfo (OIDC Discovery 응답과 동일)
GRAFANA_OIDC_API_URL=https://<IDP_HOST>/api/userinfo
GRAFANA_OIDC_LOGOUT_URL=https://<IDP_HOST>/logout
GRAFANA_ALLOWED_GROUPS=Admin,PowerUser,User
```

### 3-2. `docker-compose.yml` 적용 블록 (참고)

```yaml
grafana:
  image: grafana/grafana:latest
  env_file: [.env]
  environment:
    GF_SERVER_ROOT_URL: ${GRAFANA_ROOT_URL}
    GF_SERVER_SERVE_FROM_SUB_PATH: "true"
    # 비상용 admin은 유지하되 화면 로그인 폼은 숨김
    GF_AUTH_BASIC_ENABLED: "true"
    GF_SECURITY_ADMIN_USER: "admin"
    GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
    GF_AUTH_ANONYMOUS_ENABLED: "false"
    GF_USERS_ALLOW_SIGN_UP: "false"
    GF_AUTH_DISABLE_LOGIN_FORM: "true"
    GF_AUTH_OAUTH_AUTO_LOGIN: "true"

    GF_AUTH_GENERIC_OAUTH_ENABLED: "true"
    GF_AUTH_GENERIC_OAUTH_NAME: "MWM IDP"
    GF_AUTH_GENERIC_OAUTH_CLIENT_ID: ${GRAFANA_OIDC_CLIENT_ID}
    GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET: ${GRAFANA_OIDC_CLIENT_SECRET}
    GF_AUTH_GENERIC_OAUTH_AUTH_URL: ${GRAFANA_OIDC_AUTH_URL}
    GF_AUTH_GENERIC_OAUTH_TOKEN_URL: ${GRAFANA_OIDC_TOKEN_URL}
    GF_AUTH_GENERIC_OAUTH_API_URL: ${GRAFANA_OIDC_API_URL}
    GF_AUTH_GENERIC_OAUTH_SCOPES: "openid profile email groups"

    # mwm-idp는 PKCE 미지원
    GF_AUTH_GENERIC_OAUTH_USE_PKCE: "false"
    # self-signed cert일 때만 true. 정식 CA면 false
    GF_AUTH_GENERIC_OAUTH_TLS_SKIP_VERIFY_INSECURE: "true"

    # mwm-idp claim → Grafana 매핑
    GF_AUTH_GENERIC_OAUTH_LOGIN_ATTRIBUTE_PATH: "preferred_username"
    GF_AUTH_GENERIC_OAUTH_EMAIL_ATTRIBUTE_PATH: "email"
    GF_AUTH_GENERIC_OAUTH_NAME_ATTRIBUTE_PATH: "given_name"
    GF_AUTH_GENERIC_OAUTH_GROUPS_ATTRIBUTE_PATH: "groups"

    GF_AUTH_GENERIC_OAUTH_ALLOWED_GROUPS: ${GRAFANA_ALLOWED_GROUPS}
    GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH: "contains(groups[*], 'Admin') && 'Admin' || contains(groups[*], 'PowerUser') && 'Editor' || 'Viewer'"
    GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT: "false"
    GF_AUTH_GENERIC_OAUTH_ALLOW_ASSIGN_GRAFANA_ADMIN: "true"

    # Grafana 로그아웃 시 IDP 세션도 종료 (post_logout_redirect_uri 미사용)
    GF_AUTH_SIGNOUT_REDIRECT_URL: ${GRAFANA_OIDC_LOGOUT_URL}
  extra_hosts:
    - "<IDP_HOST_FQDN>:host-gateway"   # 사내 DNS 미해결 시
```

---

## 4. 역할 매핑 (Role Mapping)

`mwm-idp`의 `roles` JSON → Grafana 조직 역할 변환:

| IdP `roles` 항목 | Grafana 역할 | 비고 |
| :--- | :--- | :--- |
| `Admin` 포함 | `Admin` (또는 `GrafanaAdmin`) | `ALLOW_ASSIGN_GRAFANA_ADMIN=true` 상태에서 매핑식을 `'GrafanaAdmin'`으로 바꾸면 서버 어드민까지 부여 |
| `PowerUser` 포함 | `Editor` | 대시보드 편집 가능 |
| 그 외 (`User` 등) | `Viewer` | 읽기 전용 |
| `ALLOWED_GROUPS`에 없는 사용자 | 로그인 거부 | `ROLE_ATTRIBUTE_STRICT=true`로 변경하면 매핑 실패도 차단 |

운영 환경에서 `Admin`을 곧바로 `GrafanaAdmin`으로 승격하려면 `GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH`를 다음으로 변경:

```
contains(groups[*], 'Admin') && 'GrafanaAdmin' || contains(groups[*], 'PowerUser') && 'Editor' || 'Viewer'
```

---

## 5. Production 전환 체크리스트

| 항목 | Dev | Production 권장 |
| :--- | :--- | :--- |
| `Client ID` | `grafana-client` | `grafana-prod` 등 환경별 분리 |
| `Client Secret` | 공유 비밀 가능 | **새로 발급**, 비밀 관리시스템(Vault 등) 사용 |
| `Redirect URI` | `*.mwm.local:21443` | 정식 도메인 (예: `https://grafana.example.com/grafana/login/generic_oauth`) |
| `TLS_SKIP_VERIFY_INSECURE` | `true` | **`false`** (사내/공인 CA 신뢰 체인 구축) |
| `ALLOW_ASSIGN_GRAFANA_ADMIN` | `true` | `Admin` 그룹을 신뢰할 수 있을 때만 유지 |
| `GRAFANA_ADMIN_PASSWORD` | `admin1234` 등 | 운영용 강력 비밀번호. API/비상용으로만 사용 |
| `GRAFANA_ALLOWED_GROUPS` | `Admin,PowerUser,User` | 운영 정책에 맞게 좁힘 |
| Grafana 세션 쿠키 도메인 | localhost | `cookie_secure = true`, `cookie_samesite = lax` 권장 |
| IdP `OIDC_ISSUER` | `https://idp.mwm.local:20443` | 정식 issuer URL. **id_token `iss` 클레임이 이와 정확히 일치**해야 Grafana 검증 통과 |

---

## 6. 검증 절차

### 6-1. 자동화 검증 (배포 후 즉시)

```bash
# 1) Grafana 컨테이너에서 IdP discovery 도달
docker exec grafana sh -c \
  'wget --no-check-certificate -qO- https://<IDP_HOST>/.well-known/openid-configuration' | jq

# 2) Grafana 진입 시 IdP authorize URL로 302 redirect 되는지
curl -sk -i 'https://<GRAFANA_HOST>/grafana/login/generic_oauth' | grep -i '^Location:'
#   ↳ Location: https://<IDP_HOST>/oauth/authorize?client_id=grafana-client&...

# 3) IdP가 등록된 client_id + redirect_uri를 인정하는지
curl -sk -o /dev/null -w '%{http_code}\n' \
  'https://<IDP_HOST>/oauth/authorize?client_id=grafana-client&redirect_uri=<url-encoded>&response_type=code&scope=openid+profile+email+groups&state=t'
#   ↳ 200 (로그인 페이지). 400이면 client/redirect 불일치

# 4) Token endpoint client_secret_basic 인증 통과
curl -sk -u 'grafana-client:<SECRET>' -X POST 'https://<IDP_HOST>/oauth/token' \
  -d 'grant_type=authorization_code&code=invalid&redirect_uri=x'
#   ↳ {"error":"Invalid authorization code"}  ← 정상 (client 인증은 통과)
#   ↳ {"error":"Invalid client_secret"}        ← .env secret 점검 필요
```

### 6-2. 브라우저 수동 검증

1. `https://<GRAFANA_HOST>/grafana` 접속
2. 자동으로 IdP 로그인 페이지로 리다이렉트되는지 확인 (`oauth_auto_login`)
3. IdP 계정으로 로그인
4. Grafana 대시보드 진입 확인
5. 우상단 프로필 → Preferences → 역할이 `groups`에 따라 `Admin`/`Editor`/`Viewer`로 매핑됐는지 확인
6. 로그아웃 → IdP 로그인 페이지로 이동(IdP 세션도 종료)되는지 확인

### 6-3. 비상 접근 (OIDC 장애 시)

`https://<GRAFANA_HOST>/grafana/login?disableAutoLogin=true` 로 접근 → admin / `GRAFANA_ADMIN_PASSWORD`로 Basic 로그인.

---

## 7. 트러블슈팅

| 증상 | 원인 | 조치 |
| :--- | :--- | :--- |
| `redirect_uri_mismatch` 또는 IdP 400 | IdP에 등록한 redirect_uri와 Grafana 요청이 다름 | sub-path(`/grafana`) 포함 여부, 트레일링 슬래시, 호스트 일치 재확인 |
| `Invalid client_secret` | `.env` secret과 IdP DB 값 불일치 | UI Edit Client에서 secret 확인 후 `.env` 갱신, `docker compose up -d --force-recreate grafana` |
| Grafana 로그에 `failed to fetch userinfo` | `GRAFANA_OIDC_API_URL` 잘못됨 | `/api/userinfo` 사용 (NOT `/oauth/userinfo`). Discovery 응답값과 일치 확인 |
| `oauth.generic_oauth: error parsing id_token` | issuer 불일치 또는 JWKS 도달 실패 | IdP `OIDC_ISSUER` ↔ Grafana 컨테이너에서 본 호스트명이 동일해야 함. 컨테이너에 `extra_hosts` 추가 |
| 로그인은 되는데 모두 `Viewer` | `groups` 클레임 누락 또는 `ROLE_ATTRIBUTE_PATH` 매핑 불일치 | `docker logs grafana | grep -i userinfo` 로 실제 받은 claim 확인. JMESPath 표현 수정 |
| `User does not belong to allowed groups` | `ALLOWED_GROUPS`와 IdP `roles`가 다름 | IdP에서 사용자에게 `Admin`/`PowerUser`/`User` 중 하나 부여 |
| TLS handshake error | self-signed인데 `TLS_SKIP_VERIFY_INSECURE=false` | dev에서는 `true`. production은 IdP에 정식 CA 발급 후 `false` |
| 자동 로그인이 무한 루프 | Grafana 세션 쿠키가 sub-path로 저장되어 인식 실패 | `GRAFANA_ROOT_URL`이 nginx 외부 URL과 정확히 일치하는지 확인 |

---

## 8. 부록: mwm-idp가 발급하는 토큰/Claim 요약

| 필드 | 위치 | 값 예시 |
| :--- | :--- | :--- |
| `sub` | id_token / userinfo | `username` (고유) |
| `preferred_username` | id_token / userinfo | `username` |
| `email` | id_token / userinfo | `user@example.com` |
| `given_name` / `family_name` | id_token / userinfo | 동기화된 이름 |
| `groups` | id_token / userinfo | `["Admin", "User"]` 등 — Grafana 역할 매핑의 **유일한 근거** |
| `iss` | id_token | `OIDC_ISSUER` (반드시 Grafana가 보는 IdP URL과 일치) |
| `nonce` | id_token | Grafana가 보낸 nonce 그대로 반환 |
| 서명 알고리즘 | id_token | RS256, JWKS는 `/oauth/jwks` |

---

*최종 업데이트: 2026-05-05 — Phase 06 검증 완료 시점.*
