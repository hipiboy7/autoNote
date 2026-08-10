"""autoNote 애플리케이션 계층 — 오케스트레이션과 운영 도구.

이전 `program/src/` 의 평평한 모듈들을 패키지로 묶었다. 이유:
  * 모듈명이 `notify`, `watchdog`, `main` 처럼 일반적이어서 설치 시 다른 패키지와
    충돌한다 (`watchdog` 은 실제 PyPI 패키지명이다).
  * 패키지가 되면 editable 설치가 가능해져 PYTHONPATH 하드코딩을 없앨 수 있다.
    그 문자열이 12곳에 복붙돼 있었고 실제로 두 번 장애를 냈다.
"""
