# SCND 제작자 사용 가이드

## 1. 필수 설정

euddraft / EUD Editor 설정에는 아래 섹션이 필요합니다.

```ini
[scnd]
[MSQC]
[eudTurbo]
[TriggerEditor\main.eps]
```

`[MSQC]`는 SCNDgram 런처 입력 패킷과 최종 로드 데이터를 맵 안으로 동기화하는 데 사용됩니다.  

## 2. 맵 기본값

예제 상단에서 제작자가 바꾸는 값입니다.

```eps
const SAVE_KEY = "CHANGE_THIS_TO_A_LONG_PRIVATE_MAKER_KEY";
const MAP_ID = "MY_MAP_ID";
const MAP_NAME = "MY_MAP";
const MAP_DISPLAY_NAME = "내 유즈맵";
const MAP_IMAGE = "map_preview.png";
const SHEET_ID = "";
const APP_SCRIPT_ID = "";

scnd.configure(MAP_ID, SAVE_KEY, 0);
```

- `MAP_ID`  
  저장 암호화와 맵 식별에 사용하는 고정 ID입니다. 첫 배포 후 바꾸면 기존 저장과 호환되지 않습니다.

- `SAVE_KEY`  
  제작자 전용 개인 키입니다. 공개 저장소, 공개 게시글, 공개 예제 맵에 실제 키를 넣지 마세요.

- `MAP_NAME`  
  런처 화면과 저장 폴더 이름에 표시되는 이름입니다. 암호화 키는 아니므로 표시용으로만 생각하면 됩니다.

- `MAP_DISPLAY_NAME`  
  런처 사이드바에 보여줄 읽기 쉬운 맵 이름입니다. 비워두면 `MAP_NAME`이 대신 표시됩니다.

- `MAP_IMAGE`  
  런처 사이드바에 보여줄 맵 사진입니다. PNG 또는 GIF만 지원합니다.

- `SHEET_ID`  
  글로벌 변수를 사용할 때만 Google Sheet ID를 넣습니다. 전체 URL이 아니라 ID만 넣습니다.

- `APP_SCRIPT_ID`  
  서버 저장을 사용할 때만 Apps Script 웹앱 ID를 넣습니다. 비워두면 로컬 저장만 사용합니다.

- `compat_mode`  
  `scnd.configure(MAP_ID, SAVE_KEY, 0)`의 세 번째 값입니다. `0`, `1`, `2` 중 하나를 첫 배포 전에 결정합니다.

## 3. COMPAT_MODE 선택

`COMPAT_MODE`는 저장 코드 안에 스키마 정보를 얼마나 넣을지 정합니다.  
숫자가 높을수록 기존 저장과의 호환성은 좋아지지만, 저장 가능한 필드/비트 용량은 줄어듭니다.

| 모드 | 저장 코드에 들어가는 정보 | 허용되는 스키마 변경 | 한 화면 최악치 기준 최대 |
| --- | --- | --- | --- |
| `0` | 값만 bit-pack | 기존 저장 호환 변경 없음 | `7328 bit` |
| `1` | 필드별 bit width + 값 | 기존 필드 bit 증가, 뒤에 필드 추가 | `152 fields`, 최악값 `4864 bit` |
| `2` | 필드 key hash + bit width + 값 | 순서 변경, 제거, 뒤에 필드 추가, bit 증가 | `91 fields`, 최악값 `2912 bit` |

한 화면 기준 용량은 `52 chars x 11 code rows = 572 Hangul chars`입니다.  
SCND 내부 payload로는 대략 `229 dwords = 916 bytes`까지 들어갑니다.

### MODE 0

`MODE 0`은 현재 스키마를 그대로 bit-pack합니다. 저장 코드가 가장 작고 용량이 가장 큽니다.  
대신 기존 저장을 유지하려면 바인딩 순서, 바인딩 개수, 각 항목 bit 크기를 바꾸지 않는 것이 안전합니다.

추천 상황:

- 정식 배포 후 저장 스키마를 거의 바꾸지 않을 맵
- 한 화면 안에 최대한 많은 값을 넣어야 하는 맵
- 필드 수가 매우 많은 테스트/성장형 데이터

주의:

- 중간 필드를 제거하거나 순서를 바꾸면 기존 저장의 값 위치가 밀립니다.
- 기존 필드 bit를 바꾸면 스키마 해시와 총 bit 수가 달라져 로드가 실패할 수 있습니다.

### MODE 1

`MODE 1`은 각 필드의 bit width를 저장 코드에 함께 넣습니다.  
저장 코드 안에는 key 이름은 들어가지 않으므로, 기존 필드 순서는 유지해야 합니다.

허용되는 변경:

- 기존 필드의 bit 크기 증가
- 기존 바인딩 뒤에 새 필드 추가

피해야 하는 변경:

- 기존 필드 순서 변경
- 기존 필드 제거
- 기존 필드 key의 의미 변경
- 기존 bit 크기 감소

추천 상황:

- 업데이트로 최대 레벨, 점수, 아이템 수치 같은 기존 값의 상한이 늘어날 수 있는 맵
- 스키마 순서는 안정적으로 유지할 수 있지만, 뒤에 새 값을 추가할 가능성이 있는 맵

### MODE 2

`MODE 2`는 각 필드의 key hash와 bit width를 저장 코드에 함께 넣습니다.  
값을 바인딩 순서가 아니라 key로 찾기 때문에 호환성이 가장 좋습니다.

허용되는 변경:

- 기존 필드 순서 변경
- 기존 필드 제거
- 뒤에 새 필드 추가
- 기존 필드 bit 크기 증가

피해야 하는 변경:

- 기존 key 문자열 변경
- 기존 key를 다른 의미로 재사용
- 기존 bit 크기 감소

추천 상황:

- 장기 운영 중 스키마를 자주 정리할 가능성이 있는 맵
- 저장 항목을 제거하거나 순서를 재배치할 가능성이 있는 맵
- 용량보다 업데이트 안정성이 더 중요한 맵

### COMPAT_MODE에서 key가 중요한 이유

`bind_player_value("score", SCORE, 20)`의 `"score"`가 필드 key입니다.  
`MODE 2`에서는 이 key로 기존 저장 값을 현재 변수에 매칭합니다.

좋은 key 예:

```eps
scnd.bind_player_value("score", SCORE, 20);
scnd.bind_player_array("items", ITEMS, ITEM_COUNT, 10);
scnd.bind_player_array_bits("stats", STATS, 4, 6, 17, 4, 1);
```

나쁜 변경 예:

```eps
// 기존 저장의 "score" 값을 찾지 못하게 됩니다.
scnd.bind_player_value("player_score", SCORE, 20);
```

배열은 내부적으로 `items[0]`, `items[1]`처럼 위치별 key가 만들어집니다.  
배열 중간의 의미를 바꾸면 그 위치에 저장된 기존 값의 의미도 바뀌므로 주의하세요.

### `scnd.set_map_info(display_name, image)`

런처에 표시할 맵 이름과 사진을 설정합니다.  

```eps
const MAP_DISPLAY_NAME = "내 유즈맵 시즌 1";
const MAP_IMAGE = "map_preview.png";

scnd.configure(MAP_ID, SAVE_KEY, 0);
scnd.set_map_info(MAP_DISPLAY_NAME, MAP_IMAGE);
```

`display_name`은 런처 사용자가 보는 이름입니다.  
`MAP_NAME`은 저장 폴더/기본 이름이며, `display_name`은 사용자 런처 표시용 값입니다.

예:

```eps
const MAP_NAME = "MY_MAP";
const MAP_DISPLAY_NAME = "마이 RPG Remaster";
```

이미지는 아래 형식을 지원합니다.

- `.png` 파일 경로
- `.gif` 파일 경로
- `https://.../image.png` 또는 `https://.../image.gif`
- `data:image/png;base64,...`
- `data:image/gif;base64,...`
- raw base64 PNG/GIF 데이터

주의:

- JPG/WebP/BMP는 지원하지 않습니다.
- GIF는 런처 사이드바에서 애니메이션으로 표시될 수 있습니다.
- `write_mpq_manifest(..., display_name=..., image=...)`로 직접 넘길 수도 있지만, EPScript에서는 키워드 인자가 번거로울 수 있으므로 `set_map_info(...)`를 먼저 호출하는 방식을 권장합니다.

## 4. 저장 변수 바인딩

저장/로드할 값은 `onPluginStart()`에서 등록합니다.

```eps
function onPluginStart() {
    scnd.clear_bindings();
    scnd.bind_player_value("score", SCORE, 20);
    scnd.bind_player_array("single_u12", SINGLE_U12, U12_COUNT, 12);
    scnd.bind_player_array_bits("mixed", MIXED, 4, 6, 17, 4, 1);
    scnd.bind_global_array_bits(GLOBAL_TEST, 6, 17, 4, 1, 1, 10, 3, 2);
    scnd.write_mpq_manifest(MAP_ID, MAP_NAME, SHEET_ID, APP_SCRIPT_ID);
}
```

### `scnd.clear_bindings()`

이전에 등록된 바인딩을 모두 지웁니다.  
일반적으로 `onPluginStart()` 맨 앞에서 한 번 호출합니다. 같은 빌드 안에서 스키마를 다시 구성할 때 중복 등록을 막기 위해 필요합니다.

### `scnd.bind_player_value(key, array8, bits)`

플레이어별 단일값을 저장합니다.

- 배열 크기: `EUDArray(8)`
- 저장 위치: `array8[p]`

```eps
const SCORE = EUDArray(8);
scnd.bind_player_value("score", SCORE, 20);
```

`bits`는 저장할 수 있는 최대값을 결정합니다.  
예를 들어 `20bit`는 `0..1,048,575` 범위를 저장할 수 있습니다.

### `scnd.bind_player_array(key, array, count, bits)`

플레이어별 배열을 저장합니다.

- 배열 크기: `EUDArray(8 * count)`
- 저장 위치: `array[p * count + i]`

```eps
const ITEM_COUNT = 24;
const ITEMS = EUDArray(8 * ITEM_COUNT);
scnd.bind_player_array("items", ITEMS, ITEM_COUNT, 10);
```

모든 항목이 같은 bit 크기라면 이 함수를 쓰는 것이 가장 명확합니다.

### `scnd.bind_player_array_bytes(key, array, count, byte_count)`

`bind_player_array`의 byte 단위 버전입니다.

- `byte_count=1`: 항목당 8bit, `0..255`
- `byte_count=2`: 항목당 16bit, `0..65,535`
- `byte_count=3`: 항목당 24bit
- `byte_count=4`: 항목당 32bit

```eps
scnd.bind_player_array_bytes("inventory", INVENTORY, ITEM_COUNT, 1);
```

값의 범위가 byte 단위로 딱 떨어질 때 사용하면 읽기 쉽습니다.  
예를 들어 아이템 ID가 `0..255`라면 `byte_count=1`이 `bits=8`과 같은 의미입니다.

### `scnd.bind_player_array_bits(key, array, count, bits...)`

한 배열 안에서 위치마다 bit 크기가 다를 때 사용합니다.

```eps
const STAT_COUNT = 4;
const STATS = EUDArray(8 * STAT_COUNT);

scnd.bind_player_array_bits("stats", STATS, STAT_COUNT, 6, 17, 4, 1);
```

위 예시는 아래처럼 저장됩니다.

- `STATS[p * 4 + 0]`: 6bit
- `STATS[p * 4 + 1]`: 17bit
- `STATS[p * 4 + 2]`: 4bit
- `STATS[p * 4 + 3]`: 1bit

`bits`를 여러 개 넘길 때는 개수가 반드시 `count`와 같아야 합니다.  
EPScript에서는 `const BITS = [6, 17, 4, 1];` 같은 배열을 `bits` 인자로 넘기지 마세요. EPS 배열은 Python list가 아니라 `EUDArray`로 들어갈 수 있습니다. EPS에서는 `bind_player_array_bits(..., 6, 17, 4, 1)` 형태를 권장합니다.

```python
scnd.bind_player_array("stats", STATS, 4, [6, 17, 4, 1])
```

### `scnd.bind_global_array_bits(array, bits...)`

Google Sheet의 Global 코드로만 로드되는 맵 공용 값을 등록합니다.  
플레이어 개인 저장 코드에는 포함되지 않습니다.

추천 용도:

- 시즌 공용 보상표
- 서버에서 내려주는 전역 플래그
- 모든 플레이어가 같은 값을 읽어야 하는 설정

주의:

- 글로벌 값은 플레이어 저장과 별도입니다.
- `SHEET_ID`가 비어 있으면 런처가 글로벌 코드를 가져올 수 없습니다.
- 글로벌 값은 로드 전용 테스트 값으로 쓰는 편이 안전합니다.

### `scnd.write_mpq_manifest(MAP_ID, MAP_NAME, SHEET_ID, APP_SCRIPT_ID)`

런처가 맵을 인식할 수 있도록 MPQ manifest를 기록합니다.  
저장/로드 UI, 맵 이름, Sheet 연동, Apps Script 연동에 필요한 정보가 들어갑니다.
`new_user_code`도 이 함수에서 같이 생성됩니다. 제작자는 별도로 신규 유저 코드를 만들 필요가 없고, 런처도 `MAP_ID`/`MAP_KEY` 원문 없이 manifest의 코드만 전송합니다.

일반적으로 모든 바인딩을 끝낸 뒤 `onPluginStart()` 마지막에 호출합니다.

```eps
scnd.write_mpq_manifest(MAP_ID, MAP_NAME, SHEET_ID, APP_SCRIPT_ID);
```

## 5. 매 프레임 호출

모든 Human 플레이어에 대해 매 프레임 아래 함수 하나만 호출합니다.

```eps
foreach (p : EUDLoopPlayer("Human")) {
    setcurpl(p);
    scnd.run_player(p);
}
```

### `scnd.run_player(p)`

제작자용 통합 런타임 함수입니다.

처리하는 일:

- 저장/로드 상태기계 진행
- SCNDgram 런처 입력 수신
- Hangul 저장 코드 수신 및 검증
- 저장 코드 화면 표시
- 저장/로드 진행 메시지 처리
- 로드 완료/실패 후 입력 게이트 자동 닫기

`enable_load(p)` 상태에서만 런처 입력을 받아들이고, 입력이 끝나면 SCND가 자동으로 로드를 시작합니다.  
일반 제작자는 `tick_for_player`, `append_input`, `begin_load` 같은 내부형 함수 대신 `run_player(p)`만 쓰면 됩니다.

## 6. 저장 요청

제작자가 저장 버튼, 채팅 명령, 스위치 등 원하는 조건에서 호출합니다.

```eps
scnd.save(p);
```

### `scnd.save(p)`

현재 등록된 player 바인딩 값을 저장 코드로 만듭니다.

자동으로 처리하는 일:

- 로드 입력 게이트 닫기
- 이전 완료 상태 정리
- 플레이어 이름과 맵 정보 기반 암호화 컨텍스트 준비
- 저장 코드 생성
- 코드 보기 UI 표시

저장 중인 플레이어에게 다시 저장을 요청하면 내부 busy 상태에 따라 무시되거나 대기 상태가 됩니다.  
예제처럼 `scnd.busy(p)`를 확인하면 제작자 UI에서 "처리 중" 메시지를 따로 표시할 수 있습니다.

## 7. 로드 가능 시점 제어

로드 가능 상태는 제작자가 원하는 시점에만 열면 됩니다.

```eps
scnd.enable_load(p);
```

로드를 막고 싶으면 아래를 호출합니다.

```eps
scnd.disable_load(p);
```

### `scnd.enable_load(p)`

런처에서 들어오는 저장 코드를 받을 수 있게 엽니다.  
예를 들어 마을 NPC, 로드 버튼, 채팅 명령 `!loadon` 같은 조건에 연결합니다.

### `scnd.disable_load(p)`

로드 입력을 닫고 현재 입력 상태를 정리합니다.  
단순히 플래그만 내리는 함수가 아니라, 런처 입력 상태, 입력 버퍼, 이어받기 상태까지 정리해서 이후 입력을 받지 않도록 만듭니다.

정상 로드가 완료되거나 실패해도 SCND가 자동으로 로드 입력을 비활성화합니다.  
따라서 한 번 로드가 끝난 뒤에는 제작자가 다시 `enable_load(p)`를 호출하기 전까지 추가 입력을 받지 않습니다.

## 8. 상태 확인 함수

커스텀 보상 지급, 커스텀 UI 표시처럼 완료 후 추가 처리가 필요할 때만 사용합니다.

```eps
if (scnd.done(p) == 1) {
    if (scnd.ok(p) == 1) {
        // 성공 처리
    }
    else {
        // 실패 처리
    }

    scnd.reset_done(p);
}
```

- `scnd.busy(p)`  
  SCND가 해당 플레이어의 저장/로드 흐름을 점유 중이면 `1`입니다.  
  저장 코드 생성 중, 런처 로드 키 입력 수집 중, 로드 복호화/적용 중을 모두 포함합니다.
  같은 작업을 중복 요청하지 않도록 막을 때 사용합니다.

- `scnd.save_code_showing(p)`  
  저장 코드 생성이 끝난 뒤, 저장 코드가 실제로 화면에 표시되는 동안 `1`입니다.  
  저장 코드 표시 시간이 끝났거나 ESC로 표시를 넘겨 타이머가 종료되면 다시 `0`이 됩니다.
  저장 코드가 화면에 떠 있는 동안 특정 UI, 명령, 화면 효과를 막고 싶을 때 사용합니다.

- `scnd.done(p)`  
  저장 성공, 로드 성공, 저장 실패, 로드 실패를 모두 포함한 "마지막 작업 종료" 상태입니다.

- `scnd.ok(p)`  
  마지막 작업이 성공했으면 `1`, 실패했으면 `0`입니다.

- `scnd.is_new_user(p)`
  마지막 로드 성공이 런처의 `신규 플레이어 로드` 항목이었으면 `1`입니다.
  신규 로드는 기존 저장코드를 복호화하지 않으므로 player bind 변수에 값을 쓰지 않습니다. Global 변수가 있는 맵에서는 Global 코드만 기존과 동일하게 검증/적용한 뒤 신규 로드를 성공 처리합니다.

- `scnd.failed(p)`  
  마지막 작업이 실패했는지 확인합니다.

- `scnd.save_done(p)`  
  마지막 작업이 저장 성공인지 확인합니다.

- `scnd.load_done(p)`  
  마지막 작업이 로드 성공인지 확인합니다.

- `scnd.get_result(p)`  
  마지막 작업 결과 코드를 가져옵니다. 기본 메시지만 쓸 거라면 직접 볼 필요는 거의 없습니다.

- `scnd.reset_done(p)`  
  제작자가 `done(p)`를 직접 처리했다면 마지막에 호출해서 완료 상태를 정리합니다.

일반적인 저장/로드 상태 출력만 필요하다면 이 완료 확인도 직접 넣지 않아도 됩니다.  
SCND 기본 메시지는 `run_player(p)`에서 자동으로 처리됩니다.

### 신규 플레이어 로드 처리

런처의 `신규 플레이어 로드` 항목은 저장 파일을 사용하지 않고 manifest에 들어있는 신규 코드를 전송합니다.
이 코드는 `scnd.write_mpq_manifest(...)`가 빌드 시점에 `MAP_ID`, `MAP_KEY`, player 스키마를 바탕으로 생성해서 `scnd_manifest.json`의 `new_user_code`에 기록합니다.
런처는 `MAP_ID`나 `MAP_KEY` 원문을 알지 않고, manifest에서 읽은 한글 코드 문자열을 그대로 입력만 합니다.
제작자는 기존과 같이 `scnd.done(p)`와 `scnd.ok(p)`를 보고 로드 절차 완료를 판단하고, 신규 여부만 `scnd.is_new_user(p)`로 나누면 됩니다.

```eps
if (scnd.done(p) == 1) {
    if (scnd.ok(p) == 1) {
        if (scnd.is_new_user(p) == 1) {
            // 신규 플레이어: 기본값, 튜토리얼, 시작 보상 처리
        }
        else {
            // 기존 저장 파일 로드 완료
        }
    }

    scnd.reset_done(p);
}
```

## 9. 최소 예시

```eps
function beforeTriggerExec() {
    foreach (p : EUDLoopPlayer("Human")) {
        setcurpl(p);

        scnd.run_player(p);

        if (<?ChatEvent("CurrentPlayer", "!save")?>) {
            scnd.save(p);
        }

        if (<?ChatEvent("CurrentPlayer", "!loadon")?>) {
            scnd.enable_load(p);
        }

        if (<?ChatEvent("CurrentPlayer", "!loadoff")?>) {
            scnd.disable_load(p);
        }
    }
}
```

이 구조에서는 `!loadon` 이후 런처가 코드를 전송하면 SCND가 입력 수신, 검증, 복호화, 변수 할당까지 자동으로 진행합니다.
