# 안내사항
1. 해당 프로그램은 github, starcraft 외에는 일절 다른 통신을 하고 있지 않습니다.
2. 해당 프로그램은 유즈맵을 보다 즐겁게 하기 위해 배포하는 무료 프로그램입니다.
3. 해당 프로그램으로 플레이 하는 것은 프로그램 제작자와는 일절 관계가 없습니다.
    - 해당 프로그램으로 유즈맵 플레이시 생기는 불이익은 본인에게 있습니다.
4. 제작자가 불순한 의도를 가지고 배포를 하지 못하도록 최대한 설계되어있습니다.
5. 저장소를 활용하는 유즈맵의 경우 ./data/ 폴더에 저장됩니다.
	- 저장되는 값의 경우 유즈맵 제작자가 임의의 key를 부여할시 본 제작자도 복호화가 불가능합니다.
	- 파일의 내용이 손상되거나 잃어버린 경우에 따른 불이익은 본인에게 있습니다.
6. 이미지를 활용하는 유즈맵의 경우 ./img/ 폴더에 저장됩니다.
7. SCA가 복구되면 의미가 없어지는 런처입니다.
8. 제작된 런처도 바이러스에 감염될 수 있다는 사실을 잊지 마십시오.
    - 실행전 아래 링크를 통해 바이러스 유무를 확인할 수 있습니다.
    - https://www.virustotal.com/gui/home/upload
    - 구동시 일어나는 문제는 본 제작자와 상관이 없습니다.
9. svg는 단일 색상 path만 가능합니다.
	- 아래 url을 추천하지만, 별도의 사이트를 이용해도 됩니다.
	- https://game-icons.net/
10. python 코드는 오픈소스이기 때문에 누구나 수정이 가능합니다.
	- 수정했을때 불이익은 수정한 본인에게 있습니다.
	
---

# EUD UI/UX Bridge 제작자 빠른 시작

맵 제작자가 euddraft/EUD3 plugin 폴더에 넣어야 하는 런타임 파일은 기본적으로 다음 2개입니다.

```text
sc_uiux_bridge.py
sc_uiux_bridge_generated.py
```

- 저장소를 활용한다면 MSQC.py파일도 교체 필요.
    ```text
    MSQC.py
    ```

전체 함수 설명은 `API_REFERENCE.md`, 복사 가능한 예시는 `USAGE_EXAMPLES.md`를 확인하세요.

## plugin 로드 순서

`sc_uiux_plugin`을 맵 로직보다 먼저 로드합니다.

```ini
[plugins]
plugin/sc_uiux_bridge
plugin/my_map_main
```

## 가장 작은 예시

```eps
import sc_uiux_bridge as ui;

const BUTTONS = EUDArray(1);

function onPluginStart() {
    ui.init_bridge();

    foreach (cp : EUDLoopPlayer("Human")) {
        setcurpl(cp);

        if (IsUserCP()) {
            ui.disconnect_for_player(cp);

            ui.create_button_array_for_player(cp, BUTTONS, 1, 0, 0, 80, 40, 1);
            ui.set_button_inline_text_for_player(cp, BUTTONS[0], "BTN");

            ui.connect_for_player(cp);
        }
    }
}

function beforeTriggerExec() {
    ui.tick_bridge();

    foreach (cp : EUDLoopPlayer("Human")) {
        setcurpl(cp);

        if (IsUserCP()) {
            if (ui.was_button_clicked_for_player(cp, BUTTONS[0]).AtLeast(1)) {
                DisplayText("button clicked");
            }
        }
    }
}
```

## 저장소를 쓰는 맵

저장소를 사용하는 맵에서만 `enable_storage_sync()`와 `storage_bind_*`를 호출합니다.
저장소를 쓰지 않는 맵은 호출하지 않아도 됩니다.

```eps
import sc_uiux_bridge as ui;
ui.enable_storage_sync();

const LEVEL = EUDArray(8);

function onPluginStart() {
    ui.init_bridge();

    ui.storage_clear_bindings();
ui.storage_bind_player_number("level", LEVEL);
}
```

제작자 전용 secret을 추가로 섞어 암호화하려면 저장/로드 호출에 같은 secret을 넘깁니다.

```eps
const SAVE_SECRET = "MY_PRIVATE_SAVE_KEY";

ui.storage_save_bound_for_player(cp, "MY_MAP", 0, SAVE_SECRET);
ui.storage_load_bound_for_player(cp, "MY_MAP", 0, SAVE_SECRET);
```

secret을 넘기면 저장 파일명 과 암호화 key에 함께 들어갑니다.
secret이 다르면 같은 맵/닉네임/save_id라도 다른 저장 파일로 취급됩니다.

## 이미지 사용 방식 빠른 선택

이미지는 `image_id`만 공통으로 사용하고, 원본을 가져오는 방식은 제작자가 선택합니다.

### SVG path만 사용하는 경우

SVG 파일 전체가 아니라 `path d` 값만 등록합니다.
맵 제작자 PC 외부 파일이 필요 없고, 색상/크기/위치를 버튼이나 패널에서 자유롭게 지정하기 좋습니다.

```eps
import sc_uiux_bridge as ui;

const ICON_SWORD = 10000;

ui.register_path_icon(ICON_SWORD, "M 4 8 L 10 1 L 13 0 L 12 3 L 5 9 Z", 0, 0, 16, 16);

function onPluginStart() {
    ui.init_bridge();
    ui.set_image_cache_map_name("MY_MAP");
}
```

### PNG/JPG만 사용하는 경우

제작자 PC의 이미지 파일을 컴파일 시점에 SCX/SCM MPQ 안으로 넣고, C# 프로그램이 실행 중인 맵에서 꺼내 표시합니다.
사용자 PC에는 원본 이미지 파일이 없어도 됩니다.

```eps
import sc_uiux_bridge as ui;

const ICON_IMAGE = 10003;

ui.mpq_add_image_asset(ICON_IMAGE, "C:/Users/MapMaker/Downloads/icon.png");
ui.write_mpq_asset_manifest();

function onPluginStart() {
    ui.init_bridge();
    ui.enable_current_map_mpq_image_assets();
}
```

### SVG path와 PNG/JPG를 같이 사용하는 경우

두 방식 모두 같은 `image_id` 규칙을 사용합니다.
같은 ID가 중복되면 C#은 MPQ PNG/JPG를 우선 표시하므로 ID를 겹치지 않게 잡는 것을 권장합니다.

```eps
ui.register_path_icon(10000, "M 4 8 L 10 1 L 13 0 L 12 3 L 5 9 Z", 0, 0, 16, 16);
ui.mpq_add_image_asset(10003, "C:/Users/MapMaker/Downloads/icon.png");
ui.write_mpq_asset_manifest();

function onPluginStart() {
    ui.init_bridge();
    ui.enable_current_map_mpq_image_assets();
}
```

주의:

- `register_path_icon`, `mpq_add_image_asset`, `write_mpq_asset_manifest`는 top-level에서 호출합니다.
- `enable_current_map_mpq_image_assets()`는 현재 맵 경로 메모리를 읽으므로 `onPluginStart()` 안에서 호출합니다.
- SVG path만 쓸 때는 `set_image_cache_map_name()`, MPQ를 쓸 때는 `enable_current_map_mpq_image_assets()`를 사용합니다. 둘은 같은 map buffer를 사용하므로 같은 설정에서 같이 호출하지 마세요.
