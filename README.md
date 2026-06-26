# Naver Rank Checker

네이버 **통합검색** 결과에서 내 사이트가 몇 번째에 노출되는지 확인하는 도구입니다.

## 검색 기준

- **통합검색** (`ssc=tab.ur.all`) — 전체 통합 결과 순위
- **[메인] 섹션** (`urB_coR`) — 상단 메인 웹 블록 순위; 노출 시 `4[메인섹션]` 형식으로 우선 표시
- 페이지당 **15개** 결과 (`start=1, 16, 31, …`)
- 순위 미발견 시 `site:URL` 로 **인덱싱 여부** 추가 확인

## GUI 실행

```bash
python run_gui.py
```

## EXE 배포 (다른 PC에서 실행)

`build.bat` 실행 후 **`dist\NaverRankChecker` 폴더 전체**를 복사합니다.

```bash
.\build.bat
```

실행: `dist\NaverRankChecker\NaverRankChecker.exe`

- 등록 데이터: `%APPDATA%\NaverRankChecker\entries.json` (PC마다 별도 저장)
- Windows Defender 경고가 뜨면 「추가 정보 → 실행」을 선택하세요 (서명 없는 exe)

## GitHub 자동 배포 (업데이트 알림)

**최초 1회** (GitHub 로그인):

```powershell
.\scripts\setup-github.ps1
gh auth login
```

**이후 배포** (버전 자동 증가 + 빌드 + GitHub Release):

```powershell
.\deploy.bat
```

또는 Cursor 채팅에서 **「배포해줘」**라고 하면 됩니다.

다른 PC 사용자는 프로그램을 켤 때 새 버전 알림 → zip 다운로드 → 폴더 덮어쓰기.

## CLI

```bash
python -m naver_rank_checker -u https://example.com -k "키워드"
```

## 참고

- 과도한 요청 시 차단될 수 있으니 요청 간격(기본 2초)을 유지하세요.
- 데이터 저장: `%APPDATA%\NaverRankChecker\entries.json`
