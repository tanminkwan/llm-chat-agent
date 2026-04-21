# IDP 클라이언트 설정 가이드 (mwm-idp 전용)

본 문서는 `llm-chat-agent` 서비스를 `mwm-idp` 인증 공급자에 등록하기 위한 상세 설정 정보를 담고 있습니다.

## 1. 클라이언트 등록 정보 (Client Registration)

| 항목 | 설정값 (Recommended) | 비고 |
| :--- | :--- | :--- |
| **Client ID** | `llm-agent` | 서비스 식별자 |
| **Client Secret** | (임의 생성) | 백엔드 `.env` 파일에 저장 필요 |
| **Redirect URI** | `https://llm-agent.mwm.local:21443/auth/callback` | 인증 후 복귀 주소 (HTTPS 필수) |
| **Grant Type** | `Authorization Code` | 표준 인증 플로우 |
| **Auth Method** | `Client Secret Post` | 토큰 요청 시 인증 방식 |

## 2. 필수 권한 범위 (Scopes)

인증 요청 시 아래 Scopes가 반드시 허용되어야 합니다.

- `openid`: OIDC 인증 필수
- `profile`: 사용자 기본 프로필
- `email`: 이메일 정보
- `groups`: **권한(Admin/User) 매핑을 위한 그룹 정보**

## 3. 어플리케이션 연동 정보 (App Config)

IDP 등록 완료 후, 아래 정보를 프로젝트 루트의 `.env` 파일에 설정하십시오.

```ini
# .env 파일 예시
OIDC_ISSUER=https://idp.mwm.local:20443
OIDC_CLIENT_ID=llm-agent
OIDC_CLIENT_SECRET={IDP에서_발급받은_Secret}
OIDC_REDIRECT_URI=https://llm-agent.mwm.local:21443/auth/callback
```

## 4. 권한 매핑 (Role Mapping)

어플리케이션은 ID Token의 `groups` 클레임을 다음과 같이 해석합니다.

- `Admin` 포함 시: 시스템 관리자 권한 부여
- `User` 포함 시: 일반 사용자 권한 부여
