# paper-feed — 논문 아침 피드 자동 수집

컴퓨터가 꺼져 있어도 매일 새벽 논문 후보를 모아 두는 저장소입니다.

```
[GitHub Actions] 한국 04:40·05:15 (월~금)
   OpenAlex → (막히면) Crossref → Unpaywall(무료 PDF)
   → data/candidates/YYYY-MM-DD.json 커밋
        │
        ▼
[Claude 예약 작업] 한국 05:47 (월~금, 클라우드)
   후보 파일 읽기 → 관련성 판정·한국어 요약 → 피드 페이지 기록 → 알림
   (후보 파일이 없거나 비면 OpenAlex·Crossref·Europe PMC 직접 수집)
        │
        ▼
[PC 'PDF 도우미'] 아주대 PC가 켜져 있을 때 (부팅 시 자동 실행)
   autoPdf 논문 PDF를 기관 네트워크로 받아 E:\★★★★★\논문 에 저장
   피드의 'PDF 저장' 버튼도 처리. 꺼져 있던 날 것은 다음에 켤 때 이어서 받음
```

## 1. GitHub 저장소 만들기 (한 번만)

1. github.com에 로그인 → 오른쪽 위 **+** → **New repository**
   - 이름: `paper-feed` · **Public** 선택 (논문 메타데이터만 올라가며 개인 정보는 없습니다. Public이어야 Claude와 PC 도우미가 로그인 없이 읽을 수 있습니다)
2. 만든 저장소에서 **uploading an existing file** → 압축을 푼 `paper-feed` 폴더 **안의 내용 전체**(`.github` 폴더 포함)를 끌어다 놓고 **Commit changes**
   - `.github` 폴더가 안 올라가면: **Add file → Create new file** → 이름에 `.github/workflows/collect.yml` 입력 → 파일 내용 붙여넣기 → Commit
3. **Settings → Secrets and variables → Actions → New repository secret**
   - `CONTACT_EMAIL` = 본인 이메일 (OpenAlex·Crossref·Unpaywall에 예의상 알리는 연락처. 요청 제한이 크게 줄어듭니다)
   - (선택) `OPENALEX_API_KEY` = openalex.org에서 받은 API 키
4. **Actions** 탭 → 워크플로 사용 허용 → **논문 후보 수집** → **Run workflow** 로 시험 실행
   - 1~3분 뒤 `data/candidates/오늘날짜.json` 이 생기면 성공

## 2. Claude 쪽 연결

- Claude 프로젝트의 `claude/논문피드-설정.md` 에서 `GitHub 저장소:` 줄에 `아이디/paper-feed` 를 적어 두면 예약 작업이 그 저장소를 읽습니다. (Claude에게 "저장소 이름은 ○○야"라고 말해도 됩니다.)

## 3. PC 'PDF 도우미' 설치 (아주대 PC)

1. Python 설치: https://www.python.org/downloads/ — 설치 첫 화면에서 **Add python.exe to PATH** 체크
2. `pc_helper` 폴더를 PC의 고정된 위치(예: `C:\paper-feed\pc_helper`)에 복사
3. `설치.bat` 더블클릭 → 메모장이 열리면 `"github_repo": "아이디/paper-feed"` 입력 후 저장·닫기
   - 윈도우 시작 시 자동 실행되도록 등록되고, 바로 실행됩니다
4. (선택) ScienceDirect(Elsevier) 논문도 자동 저장하려면 https://dev.elsevier.com 에서 무료 API 키를 받아 `helper_config.json` 의 `elsevier_api_key` 에 넣으세요. 기관 IP에서만 PDF가 나옵니다.
5. (선택) `contact_email` 을 넣으면 Unpaywall로 무료 공개본도 찾습니다.

확인 방법: `helper.log` (기록), 저장 폴더의 `_직접받기_목록.html` (로봇 확인·구독 범위 밖이라 자동 저장이 안 된 논문), `지금_한번_받기.bat` (즉시 실행).

## 자주 묻는 것

- **주제·키워드·저널 바꾸기**: 이 저장소의 `config.json` 을 GitHub 웹에서 고치세요(연필 아이콘). Claude 설정 문서도 같이 고치면 피드 선별에 바로 반영됩니다.
- **수집 시각**: `.github/workflows/collect.yml` 의 cron(UTC 기준).
- **60일 규칙**: GitHub는 활동 없는 저장소의 예약 실행을 멈추지만, 이 저장소는 매일 커밋되므로 계속 돕니다. 혹시 멈추면 Actions 탭에서 다시 켜세요.
- **출판사 접속 원칙**: 도우미는 로봇 확인이나 로그인을 우회하지 않습니다. 기관 구독 범위 안에서 개인 연구용으로 하루 수십 편 이내만 받으며, 요청 사이에 간격을 둡니다.
