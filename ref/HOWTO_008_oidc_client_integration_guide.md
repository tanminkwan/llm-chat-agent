# HOWTO_008: OAuth2/OIDC Client Integration Guide (mwm-idp)

본 문서는 외부 시스템(OAuth2 Client)이 `mwm-idp` 서버를 인증 공급자(OIDC Provider)로 사용하여 싱글 사인온(SSO) 및 사용자 인증을 구현할 때 필요한 절차와 기술 명세를 상세히 안내합니다.

---

## 1. 기본 정보 (Base Information)

*   **OIDC Provider (Issuer)**: `https://idp.mwm.local:20443`
*   **Discovery Endpoint**: `https://idp.mwm.local:20443/.well-known/openid-configuration`
*   **지원 알고리즘**: RS256 (RSA Signature with SHA-256)
*   **지원 요청 방식**: `Authorization Code Flow` (권장)

---

## 2. 클라이언트 등록 절차 (Client Registration)

인증을 시도하기 전, `mwm-idp` 관리 도구(Admin Console)를 통해 클라이언트를 등록해야 합니다.

1.  **Client ID / Secret**: 고유 식별자와 비밀키를 발급받습니다.
2.  **Redirect URI**: 인증 성공 후 사용자를 되돌려 보낼 URL을 **정확히** 등록해야 합니다. (정규 표현식 또는 와일드카드 사용 지양, `https` 권장)
3.  **Scopes**: 기본적으로 `openid`, `profile`, `email`, `groups`를 사용할 수 있도록 등록해야 합니다.

---

## 3. 기술 명세 및 엔드포인트 (Technical endpoints)

Discovery 엔드포인트를 사용하면 아래 정보를 자동으로 가져올 수 있습니다.

| 엔드포인트 기능 | URL | 설명 |
| :--- | :--- | :--- |
| **Authorization** | `/oauth/authorize` | 사용자가 로그인하고 권한을 승인하는 페이지 |
| **Token** | `/oauth/token` | Authorization Code를 Access/ID Token으로 교환 |
| **JWKS (Public Key)** | `/oauth/jwks` | 발급된 ID Token의 서명을 검증하기 위한 공개키 집합 |
| **UserInfo** | `/api/userinfo` | Access Token을 사용하여 세부 사용자 프로필 획득 |

---

## 4. 인증 플로우 단계별 가이드 (Authorization Code Flow)

### Step 1: 인증 요청 (Authorization Request)
브라우저를 통해 아래 주소로 사용자를 리다이렉트합니다.
```http
GET https://idp.mwm.local:20443/oauth/authorize?
    response_type=code&
    client_id={YOUR_CLIENT_ID}&
    redirect_uri={REGISTERED_REDIRECT_URI}&
    scope=openid profile email groups&
    state={CSRF_TOKEN}&
    nonce={RANDOM_NONCE}
```

### Step 2: 코드 획득 및 교환 (Token Request)
인증 성공 후 Redirect URI로 전달된 `code` 값을 서버-to-서버 통신으로 토큰과 교환합니다.
```http
POST https://idp.mwm.local:20443/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&
code={RECEIVED_CODE}&
redirect_uri={REGISTERED_REDIRECT_URI}&
client_id={YOUR_CLIENT_ID}&
client_secret={YOUR_CLIENT_SECRET}
```

---

## 5. ID Token 및 클레임 활용법 (Claims & ID Token)

`mwm-idp`는 RS256 방식으로 서명된 JWT 형태의 `id_token`을 제공합니다.

### 주요 클레임 (Claims)
*   **`sub`**: 사용자의 고유 식별자 (ID)
*   **`preferred_username`**: 사용자 로그인 아이디
*   **`groups`**: 사용자가 속한 권한/그룹 리스트 (예: `['Admin', 'User']`)
*   **`policy`**: (Custom) `mwm-idp`에서 정의한 특정 정책값 리스트

### 서명 검증 (Signature Verification)
1.  `id_token`의 헤더에서 `kid`를 확인합니다.
2.  `https://idp.mwm.local:20443/oauth/jwks` 주소에서 해당 `kid`와 일치하는 공개키를 가져옵니다.
3.  RSA 알고리즘을 사용하여 서명을 검증합니다. (개발 언어별 표준 JWT 라이브러리 사용 권장)

---

## 6. 개발 시 주의 사항 (Best Practices)

1.  **SSL/TLS 필수**: 모든 통신은 `https`를 통해야 하며, 특히 `redirect_uri`가 `https`가 아닌 경우 보안 경고가 발생할 수 있습니다.
2.  **State 파라미터**: CSRF 공격 방지를 위해 반드시 예측 불가능한 `state` 값을 생성하고 검증하십시오.
3.  **Discovery 활용**: 엔드포인트 URL을 하드코딩하지 말고 Discovery(`/.well-known/openid-configuration`) 결과를 캐싱하여 사용하십시오.
4.  **Token 만료**: `id_token`과 `access_token`의 만료 시간을 확인하고, 필요 시 `refresh_token`을 사용하여 갱신하십시오.

---

## 7. 트러블슈팅 (Troubleshooting)

*   **`invalid_client`**: Client ID 또는 Secret이 틀리거나 등록되지 않은 경우.
*   **`redirect_uri_mismatch`**: 요청에 포함된 `redirect_uri`가 IDP 서버에 등록된 값과 **글자 하나라도** 다를 경우 발생 (포트 번호 유무 확인 필수).
*   **`invalid_scope`**: 등록되지 않은 Scope를 요청한 경우.
*   **포트 유실 문제**: 부하 분산(L4)이나 프록시(Nginx) 사용 시 `:20443` 포트가 유실되지 않도록 `Host` 헤더 설정을 확인하십시오.
