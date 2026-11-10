# 개발 웹 CORS

개발 웹 `http://localhost:8082`에서 개발 API의 JSON을 읽을 수 있도록 서버 환경 파일에 다음을 설정한다.

```dotenv
DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:8082
```

이 값은 브라우저 주소창의 origin(스킴·호스트·포트)이며 API 주소가 아니다. 다른 포트나 127.0.0.1을 실제로 사용할 때는 해당 origin을 쉼표로 추가하고 API를 다시 배포한다. 기본 허용 목록은 비어 있으며 운영 환경에는 개발 origin을 자동 추가하지 않는다.

허용 범위는 `/v1/`이며 JSON Content-Type, Authorization, Idempotency-Key를 사용하는 OPTIONS 사전 요청도 지원한다. 인증·권한과 CSRF 검사는 유지한다. 쿠키 기반 교차 사이트 로그인은 이 설정의 범위가 아니며 credentials 허용이나 CSRF 신뢰 origin을 자동 추가하지 않는다. FE는 일반 fetch를 사용하고 `no-cors`를 사용하지 않는다.

현재 개발 SSM 배포는 `/etc/ye-develop-af08004.env`를 읽는다. 해당 파일에 허용 origin을 저장하면 다음 개발 배포에도 유지된다. 비밀값을 출력하거나 저장소에 환경 파일을 커밋하지 않는다.

검증은 허용 origin의 GET·OPTIONS 응답, 다른 origin 비허용, 오류 응답, origin 없는 기존 요청을 포함한다. CORS 성공만으로 실제 로그인·쓰기·전체 API 명세 일치를 판정하지 않는다.
