# SCNDUI API 제작자 설명서
- url : https://hhya.pe.kr/

## 빠른 시작

```python
import scndui

# 1. 화면 기준 좌표계 설정. 기본값은 1600x1200입니다.
scndui.set_base_size(1600, 1200)

# 2. 이미지 등록. ID를 생략하면 자동 번호가 할당됩니다.
logo = scndui.add_image("assets/logo.png")

# 3. UI 생성
scndui.panel("menu_panel", scndui.CENTER, 0, 0, 360, 220, "메뉴")
scndui.theme("menu_panel", "panel")
scndui.hidden("menu_panel")

scndui.button("menu_button", scndui.BL, 40, 35, 120, 36, "메뉴")
scndui.theme("menu_button", "primary")
scndui.action("menu_button", scndui.toggle("menu_panel"))

# 4. 맵에 uiux_init.json과 이미지 파일을 기록
scndui.write()
```

## 좌표와 기준점

대부분의 요소 생성 함수는 `anchor, x, y, width, height` 순서로 위치와 크기를 받습니다.

| 상수 | 값 | 의미 |
| --- | --- | --- |
| `scndui.TL` | `"tl"` | 좌상단 |
| `scndui.T` | `"t"` | 상단 중앙 |
| `scndui.TR` | `"tr"` | 우상단 |
| `scndui.L` | `"l"` | 좌측 중앙 |
| `scndui.CENTER` | `"c"` | 중앙 |
| `scndui.R` | `"r"` | 우측 중앙 |
| `scndui.BL` | `"bl"` | 좌하단 |
| `scndui.B` | `"b"` | 하단 중앙 |
| `scndui.BR` | `"br"` | 우하단 |

```python
scndui.button("ok", scndui.CENTER, 0, 180, 140, 38, "확인")
scndui.button("close", scndui.TR, 12, 12, 44, 44, "X")
```

## 기본 요소 생성

### `panel(panel_id, anchor=TL, x=0, y=0, width=160, height=48, text="")`

최상위 패널을 만듭니다. 패널은 배경, 안내창, 페이지, 컨테이너 역할에 적합합니다.

```python
scndui.panel("guide", scndui.CENTER, 0, 0, 800, 600, "설명")
scndui.theme("guide", "panel")
scndui.pass_click("guide")
```

### `panel_to(parent_id, panel_id, anchor=TL, x=0, y=0, width=160, height=48, text="")`

부모 요소 안에 자식 패널을 만듭니다. 좌표는 부모 기준입니다.

```python
scndui.panel("root", scndui.CENTER, 0, 0, 500, 400, "")
scndui.panel_to("root", "page_1", scndui.TL, 20, 20, 460, 320, "1페이지")
```

### `button(button_id, anchor=TL, x=0, y=0, width=120, height=36, text="")`

최상위 버튼을 만듭니다.

```python
scndui.button("open_menu", scndui.BL, 40, 35, 120, 36, "메뉴")
scndui.action("open_menu", scndui.show("menu_panel"))
```

### `button_to(parent_id, button_id, anchor=TL, x=0, y=0, width=120, height=36, text="")`

부모 요소 안에 자식 버튼을 만듭니다.

```python
scndui.button_to("guide", "close_guide", scndui.TR, 8, 8, 48, 48, "X")
scndui.action("close_guide", scndui.hide("guide"))
```

### `input_box(input_id, anchor=TL, x=0, y=0, width=160, height=34, text="", input_type="all", placeholder="")`

최상위 입력창을 만듭니다.

`input_type`은 런처 입력 제한 힌트입니다. 보통 `"all"`을 씁니다.

```python
scndui.input_box("code_input", scndui.TL, 722, 491, 160, 34, "", "all", "코드 입력")
```

### `input_to(parent_id, input_id, anchor=TL, x=0, y=0, width=160, height=34, text="", input_type="all", placeholder="")`

부모 요소 안에 입력창을 만듭니다.

```python
scndui.input_to("guide", "name_input", scndui.TL, 40, 80, 220, 34, "", "all", "이름")
```

### `panel_def()`, `button_def()`, `input_def()`

문서에 바로 추가하지 않고 요소 딕셔너리만 만듭니다. `build()`로 직접 문서를 조립할 때 사용합니다.

```python
root = scndui.panel_def("root", scndui.CENTER, 0, 0, 400, 300, "루트")
ok = scndui.button_def("ok", scndui.B, 0, 20, 120, 36, "확인", action=scndui.hide("root"))

doc = scndui.build(panels=[root], buttons=[ok])
scndui.write_uiux_init(doc)
```

### 하위 호환 별칭

| 별칭 | 실제 함수 |
| --- | --- |
| `add_panel`, `create_panel`, `f_panel` | `panel` |
| `add_panel_to`, `f_panel_to` | `panel_to` |
| `add_button`, `create_button`, `f_button` | `button` |
| `add_button_to`, `f_button_to` | `button_to` |
| `create_input`, `f_input`, `f_input_box` | `input_box` |
| `define_panel`, `f_panel_def` | `panel_def` |
| `define_button`, `f_button_def` | `button_def` |
| `define_input`, `f_input_def` | `input_def` |

## 간단 생성 문자열

### `create(spec)`

`"key=value; key=value"` 형태로 패널, 버튼, 입력창, 이미지를 만듭니다. 툴에서 자동 생성하기 쉬운 형식입니다.

```python
scndui.create("panel; id=window; a=c; x=0; y=0; w=500; h=300; text=창; theme=window")
scndui.create("button; id=close; parent=window; a=tr; x=8; y=8; w=48; h=48; text=X; action=hide:window")
scndui.create("input; id=code; a=tl; x=40; y=80; w=180; h=34; placeholder=코드")
scndui.create("image; id=logo; path=assets/logo.png")
```

### `create_to(parent_id, spec)`

`create()`와 같지만 부모를 고정합니다.

```python
scndui.create_to("window", "button; id=ok; a=b; x=0; y=20; w=120; h=36; text=확인")
```

## 스타일과 표시 상태

### `theme(element_id, theme_name)`

미리 정의된 테마를 적용합니다.

지원 테마:

| 이름 | 용도 |
| --- | --- |
| `"window"` | 큰 창, 안내창 |
| `"panel"` | 일반 패널 |
| `"pass"` | 클릭 통과용 버튼/패널 |
| `"primary"` | 기본 버튼 |
| `"danger"` | 위험/삭제 버튼 |
| `"success"` | 성공/확인 버튼 |
| `"accent"` | 강조 버튼 |
| `"ghost"` | 닫기 버튼, 보조 버튼 |

```python
scndui.theme("menu_button", "primary")
scndui.theme("guide", "window")
```

### `colors(element_id, fill_color="", outline_color="", text_color="", opacity="")`

배경색, 외곽선, 글자색, 불투명도를 설정합니다.

```python
scndui.colors("guide", "#1f2937", "#334155", "#d1d5db", 82)
```

### `style(element_id, fill_color="", outline_color="", text_color="", opacity="", radius="", font_size="", animation="", animation_ms="", animation_direction="")`

색상, 모서리, 폰트, 애니메이션을 한 번에 설정합니다.

```python
scndui.style("guide", "#111827", "#94a3b8", "#ffffff", 86, 8, 15, "fade", 160)
```

### `font(element_id, font_size)`

최대 폰트 크기를 설정합니다. 런처는 내용이 넘치면 자동 축소합니다.

```python
scndui.font("title", 24)
```

### `radius(element_id, value)`

모서리 둥글기를 설정합니다.

```python
scndui.radius("guide", 6)
```

### `appear(element_id, animation="fade", animation_ms=160, direction="")`

요소가 표시될 때 애니메이션을 적용합니다.

`animation` 예: `"fade"`, `"slide"`, `"scale"`, `"none"`  
`direction` 예: `"up"`, `"down"`, `"left"`, `"right"`

```python
scndui.appear("menu_panel", "slide", 200, "up")
scndui.appear("close_button", "fade", 180)
```

### `visible(element_id, enabled=1)` / `hidden(element_id)`

초기 표시 여부를 정합니다.

```python
scndui.visible("guide", 1)
scndui.hidden("popup")
```

### `auto_scale(element_id, enabled=1)`

스타크래프트 클라이언트 크기에 맞춰 자동 스케일링할지 정합니다.

```python
scndui.auto_scale("fixed_pixel_panel", 0)
```

### `layout(element_id, align="", valign="", auto_scale=1, visible=1)`

텍스트 정렬, 자동 스케일, 초기 표시를 한 번에 설정합니다.

```python
scndui.layout("guide", align="center", valign="center", auto_scale=1, visible=0)
```

## 좌표와 크기 변경

### `rect(element_id, anchor="", x="", y="", width="", height="")`

요소의 기준점, 위치, 크기를 수정합니다.

```python
scndui.rect("guide", scndui.CENTER, 0, 0, 800, 600)
```

### `move(element_id, x, y, anchor="")`

위치를 수정합니다. `anchor`를 비우면 기존 기준점을 유지합니다.

```python
scndui.move("menu_button", 40, 35, scndui.BL)
```

### `size(element_id, width, height)`

크기만 수정합니다.

```python
scndui.size("guide", 700, 500)
```

### `reference(element_id, target_id="", edge="", anchor="", x="", y="")`

다른 요소를 기준으로 상대 배치합니다.

```python
scndui.button("menu", scndui.BL, 40, 35, 120, 36, "메뉴")
scndui.panel("menu_panel", scndui.T, 20, -266, 155, 251, "")
scndui.reference("menu_panel", "menu")
```

### `relative_to(element_id, target_id="", edge="", anchor="", x="", y="")`

`reference()`의 별칭입니다.

### `clear_reference(element_id)`

상대 배치를 해제하고 부모/화면 기준 좌표로 되돌립니다.

```python
scndui.clear_reference("menu_panel")
```

## 텍스트와 정렬

### `set_text(element_id, value)`

초기 텍스트를 설정합니다.

```python
scndui.set_text("guide", "새 설명입니다.")
```

### `align(element_id, horizontal="center", vertical="center")`

기본 가로/세로 정렬을 설정합니다.

`horizontal`: `"left"`, `"center"`, `"right"`  
`vertical`: `"top"`, `"center"`, `"bottom"`

```python
scndui.align("guide", "center", "top")
```

### `line_text_align(element_id, *alignments)`

줄별 가로 정렬을 설정합니다. 빈 문자열은 기본 정렬을 사용합니다.

```python
scndui.set_text("guide", "좌측 줄\n중앙 줄\n우측 줄")
scndui.align("guide", "center", "center")
scndui.line_text_align("guide", "left", "center", "right")
```

### `text_line_align(element_id, *alignments)`

`line_text_align()`의 별칭입니다.

### `rich(text, color="", font_size="", bold=0)`

리치 텍스트 한 조각을 만듭니다.

```python
red = scndui.rich("위험", "#ef4444", 18, 1)
normal = scndui.rich(" 지역입니다.", "#ffffff", 14)
```

### `rich_text(element_id, *runs)`

초기 텍스트를 리치 텍스트로 설정합니다.

```python
scndui.rich_text(
    "guide",
    scndui.rich("저주 정렬", "#ef4444", 18),
    scndui.rich(" 테스트", "#60a5fa", 24, 1),
)
```

## 이미지 등록

### `set_image_folder(path="")`

상대 경로 이미지의 기본 폴더를 설정합니다.

```python
scndui.set_image_folder("assets/ui")
scndui.add_image(1, "logo.png")
```

### `image_folder(path="")`

`set_image_folder()`의 별칭입니다.

### `get_image_folder()`

현재 이미지 기본 폴더를 반환합니다.

```python
current = scndui.get_image_folder()
```

### `image(image_id, path=None, mpq_path=None)`

이미지 에셋 딕셔너리를 만듭니다. 문서에 바로 추가하지는 않습니다.

`image_id`를 생략하고 경로만 넣으면 자동 ID가 할당됩니다.

```python
logo_asset = scndui.image(1, "assets/logo.png")
auto_asset = scndui.image("assets/auto.png")
```

### `add_image(image_id, path=None, mpq_path=None)`

현재 문서에 제작자 이미지를 등록합니다.

```python
scndui.add_image(1, "assets/logo.png")
scndui.add_image(2, "data:image/png;base64,...")
```

### `register_image(image_id, path=None, mpq_path=None)`

`add_image()`의 별칭입니다.

```python
scndui.register_image(4, "assets/icon.png")
```

### `next_image_id()`

현재 문서에서 사용 가능한 다음 이미지 번호를 반환합니다.

```python
image_id = scndui.next_image_id()
scndui.add_image(image_id, "assets/new_icon.png")
```

## 요소 이미지

### `image_on(element_id, image_id, builtin_image=0, image_position="left", image_width="", image_height="", image_x="", image_y="", image_layout="")`

패널/버튼에 이미지를 붙입니다.

```python
scndui.add_image(1, "assets/sword.png")
scndui.image_on("attack_button", 1, 0, "left", 24, 24)
```

### `icon(element_id, image_id, builtin_image=0, position="left", width="", height="", x="", y="", layout="")`

`image_on()`의 짧은 별칭입니다.

```python
scndui.icon("attack_button", 1, 0, "left", 24, 24)
```

### `background_image(element_id, image_id, builtin_image=0, width="", height="", x="", y="", position="c")`

요소의 배경 이미지로 설정합니다.

```python
scndui.add_image(2, "assets/panel_bg.png")
scndui.background_image("guide", 2, 0, 500, 300, 0, 0, "c")
```

### `content_image(image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode="")`

내용 변경 액션이나 초기 콘텐츠에 넣을 이미지 정보를 만듭니다.

`mode="text"`로 지정하면 텍스트 안의 `{img:N}` 토큰 위치에 이미지가 렌더링됩니다.

```python
scndui.add_image(4, "assets/gem.png")

scndui.action(
    "show_result",
    scndui.content(
        "result",
        "보상 {img:4} 획득",
        scndui.content_image(4, 0, "c", 24, 24, mode="text"),
    ),
)
```

### `content_image_on(element_id, image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode="")`

초기 콘텐츠 이미지로 요소에 바로 추가합니다.

```python
scndui.content_image_on("guide", 4, 0, "br", 64, 64, -20, -20)
```

### `content_icon(element_id, image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode="")`

`content_image_on()`의 별칭입니다.

## 클릭 처리

### `block_click(element_id, enabled=1)`

패널 영역이 스타크래프트 마우스 클릭을 막도록 설정합니다. 패널에만 사용할 수 있습니다.

```python
scndui.block_click("modal", 1)
```

### `pass_click(element_id)`

패널이 클릭을 막지 않고 스타크래프트로 통과시킵니다.

```python
scndui.pass_click("floating_info")
```

## 액션 기본

액션은 문자열로 인코딩됩니다. 버튼에는 `action(button_id, 액션)`으로 연결합니다.

### `action(element_id, value)` / `set_action(element_id, value)`

버튼에 액션을 지정합니다.

```python
scndui.action("menu_button", scndui.toggle("menu_panel"))
```

### `actions(*items)` / `multi(*items)` / `action_many(*items)`

여러 액션을 하나로 묶습니다.

```python
scndui.action(
    "open_guide",
    scndui.actions(
        scndui.show("guide_panel"),
        scndui.hide("padding_panel"),
        scndui.log("guide opened"),
    ),
)
```

## 표시 액션

### `show(element_id)` / `action_show(element_id)`

요소를 표시합니다.

```python
scndui.action("open", scndui.show("panel_1"))
```

### `hide(element_id)` / `action_hide(element_id)`

요소를 숨깁니다.

```python
scndui.action("close", scndui.hide("panel_1"))
```

### `toggle(element_id)` / `action_toggle(element_id)`

표시/숨김 상태를 토글합니다.

```python
scndui.action("menu", scndui.toggle("menu_panel"))
```

### `hide_self()` / `action_hide_self()`

클릭한 버튼 자신을 숨깁니다.

```python
scndui.action("once_button", scndui.hide_self())
```

### `show_all()` / `action_show_all()`

UIUX 액션으로 숨겨진 모든 요소를 표시합니다.

```python
scndui.action("debug_show_all", scndui.show_all())
```

### `show_only(element_id, scope_id="")` / `action_show_only(element_id, scope_id="")`

같은 범위 안에서 하나만 보여주고 나머지는 숨깁니다.

`scope_id`를 지정하면 그 부모/범위 안에서만 동작합니다. `element_id`, `scope_id`에는 `:`, `,`, `|`, `;`, 줄바꿈을 넣으면 안 됩니다.

```python
scndui.action("tab_1_button", scndui.show_only("tab_1_panel", "tab_root"))
scndui.action("tab_2_button", scndui.show_only("tab_2_panel", "tab_root"))
```

### `show_one(element_id, *other_ids)`

하나를 표시하고, 함께 넘긴 다른 요소를 명시적으로 숨깁니다.

```python
scndui.action("show_a", scndui.show_one("panel_a", "panel_b", "panel_c"))
```

### `show_many(*element_ids)` / `hide_many(*element_ids)`

여러 요소를 한 번에 표시하거나 숨깁니다.

```python
scndui.action("open_all", scndui.show_many("a", "b", "c"))
scndui.action("close_all", scndui.hide_many("a", "b", "c"))
```

### `log(message)` / `action_log(message)`

런처 디버그 로그를 남깁니다.

```python
scndui.action("debug", scndui.log("clicked debug button"))
```

## 내용 변경 액션

### `text(element_id, value, images=None)` / `action_text(element_id, value, images=None)`

런타임에 텍스트를 변경합니다. 단순 텍스트 변경에 적합합니다.

```python
scndui.action("change", scndui.text("result", "변경 완료"))
```

### `content(element_id, value="", *items)` / `action_content(element_id, text="", *items)`

런타임에 텍스트, 콘텐츠 이미지, 정렬 정보를 함께 변경합니다.

```python
scndui.action(
    "show_reward",
    scndui.content(
        "result",
        "보상 {img:4}",
        scndui.content_image(4, 0, "c", 24, 24, mode="text"),
        scndui.content_align("center"),
        scndui.content_valign("center"),
    ),
)
```

### `content_rich(element_id, *items)` / `action_content_rich(element_id, *items)`

런타임에 리치 텍스트로 내용을 변경합니다.

```python
scndui.action(
    "warn",
    scndui.content_rich(
        "result",
        scndui.rich("위험", "#ef4444", 18, 1),
        scndui.rich(" 상태입니다.", "#ffffff", 14),
        scndui.content_align("center"),
    ),
)
```

### `content_align(horizontal="")`

`content()`나 `content_rich()`에 넣는 가로 정렬 옵션입니다.

```python
scndui.content_align("right")
```

### `content_valign(vertical="")`

`content()`나 `content_rich()`에 넣는 세로 정렬 옵션입니다.

```python
scndui.content_valign("bottom")
```

### `line_alignments(*values)` / `line_align(*values)`

내용 변경 액션에서 줄별 정렬을 지정합니다.

```python
scndui.action(
    "change_lines",
    scndui.content(
        "result",
        "왼쪽\n중앙\n오른쪽",
        scndui.line_align("left", "center", "right"),
    ),
)
```

### `change_text(element_id, value, images=None)` / `set_text_action(element_id, value, images=None)`

`text()`의 별칭입니다.

## 모양 변경 액션

### `style_action(element_id, fill_color="", outline_color="", text_color="", opacity="", radius="", font_size="", rich_text=None)`

런타임에 색상, 불투명도, 모서리, 폰트, 리치 텍스트 스타일을 변경합니다.

```python
scndui.action(
    "danger_mode",
    scndui.style_action("result", "#7f1d1d", "#ef4444", "#ffffff", 90, 6, 16),
)
```

### `rect_action(element_id, anchor="", x="", y="", width="", height="")`

런타임에 기준점, 위치, 크기를 변경합니다.

```python
scndui.action("move_result", scndui.rect_action("result", scndui.CENTER, 0, -120, 500, 180))
```

## 조건 액션

조건 액션은 조건이 참일 때 내부 액션을 실행합니다.

### 입력값 조건

| 함수 | 의미 |
| --- | --- |
| `if_match(input_id, expected, *actions)` | 입력값이 일치 |
| `if_not_match(input_id, expected, *actions)` | 입력값이 일치하지 않음 |
| `if_ge(input_id, expected, *actions)` | 숫자 입력값이 `>=` |
| `if_le(input_id, expected, *actions)` | 숫자 입력값이 `<=` |
| `if_gt(input_id, expected, *actions)` | 숫자 입력값이 `>` |
| `if_lt(input_id, expected, *actions)` | 숫자 입력값이 `<` |

숫자 비교는 입력값과 비교값이 숫자 또는 숫자 계산식으로 해석될 때만 참입니다.

```python
scndui.input_box("code_input", scndui.TL, 700, 500, 180, 34, "", "all", "코드")
scndui.panel("result", scndui.CENTER, 0, 120, 360, 120, "")

scndui.button("check_code", scndui.TL, 740, 550, 120, 36, "확인")
scndui.action(
    "check_code",
    scndui.if_match(
        "code_input",
        "1234",
        scndui.content("result", "코드가 일치합니다.", scndui.content_align("center")),
        scndui.elseif_match(
            "code_input",
            "12345",
            scndui.content("result", "2번째 코드를 찾으셨군요.", scndui.content_align("center")),
        ),
        scndui.else_do(
            scndui.content("result", "그런 코드는 없습니다.", scndui.content_align("center"))
        ),
    ),
)
```

### 변수 조건

| 함수 | 의미 |
| --- | --- |
| `if_var(variable_id, expected, *actions, op="eq")` | 변수 조건 직접 지정 |
| `if_var_match(variable_id, expected, *actions)` | 변수값 일치 |
| `if_var_not_match(variable_id, expected, *actions)` | 변수값 불일치 |
| `if_var_ge(variable_id, expected, *actions)` | 숫자 변수값 `>=` |
| `if_var_le(variable_id, expected, *actions)` | 숫자 변수값 `<=` |
| `if_var_gt(variable_id, expected, *actions)` | 숫자 변수값 `>` |
| `if_var_lt(variable_id, expected, *actions)` | 숫자 변수값 `<` |

```python
scndui.variable("score", "int32", 0)
scndui.panel("score_text", scndui.TL, 20, 20, 200, 40, "점수: {var:score}")

scndui.button("reward", scndui.TL, 20, 80, 160, 36, "보상 확인")
scndui.action(
    "reward",
    scndui.if_var_ge(
        "score",
        10,
        scndui.content("score_text", "보상 가능: {var:score}점"),
        scndui.else_do(scndui.content("score_text", "점수가 부족합니다: {var:score}점")),
    ),
)
```

### 표시 상태 조건

| 함수 | 의미 |
| --- | --- |
| `if_visible(element_id, *actions)` | 요소가 보이면 실행 |
| `if_hidden(element_id, *actions)` | 요소가 숨겨져 있으면 실행 |
| `elseif_visible(element_id, *actions)` | elseif 표시 조건 |
| `elseif_hidden(element_id, *actions)` | elseif 숨김 조건 |

```python
scndui.action(
    "toggle_hint",
    scndui.if_visible(
        "hint_panel",
        scndui.hide("hint_panel"),
        scndui.else_do(scndui.show("hint_panel")),
    ),
)
```

### 조건식

### `if_expr(expression, *actions)` / `elseif_expr(expression, *actions)`

`AND`, `OR`, 괄호를 사용해 복합 조건을 작성합니다. 각 조건 원자는 대괄호 `[]` 안에 씁니다.

조건 원자 예:

```text
[input:code_input == 1234]
[var:score >= 10]
[guide_panel visible]
[guide_panel hidden]
```

복합 조건 예:

```python
scndui.action(
    "claim",
    scndui.if_expr(
        "([var:score >= 10] AND [guide_panel hidden]) OR [input:admin_code == 9999]",
        scndui.content("result", "획득 가능"),
        scndui.else_do(scndui.content("result", "조건 부족")),
    ),
)
```

주의: 조건식의 원자는 반드시 `[]`로 감싸야 합니다.

### `elseif_match(...)`, `elseif_not_match(...)`, `elseif_ge(...)`, `elseif_le(...)`, `elseif_gt(...)`, `elseif_lt(...)`

입력값 조건의 `else if` 분기입니다.

```python
scndui.if_match(
    "input_1",
    "A",
    scndui.content("result", "A"),
    scndui.elseif_match("input_1", "B", scndui.content("result", "B")),
    scndui.elseif_gt("input_1", 100, scndui.content("result", "100 초과")),
    scndui.else_do(scndui.content("result", "기타")),
)
```

### `elseif_var(...)`

변수 조건의 `else if` 분기입니다.

```python
scndui.if_var_ge(
    "score",
    100,
    scndui.content("result", "100점 이상"),
    scndui.elseif_var("score", "50", scndui.content("result", "50점"), op="ge"),
)
```

### `else_do(*actions)`

앞 조건이 모두 거짓일 때 실행할 액션입니다.

```python
scndui.else_do(scndui.content("result", "조건에 맞지 않습니다."))
```

### `condition(input_id, expected_value="", *then_actions)`

`if_match()`의 별칭입니다.

## 변수

### `variable(variable_id, type_name="string", initial_value="")`

런타임 변수를 선언합니다. 변수는 텍스트 표시, 조건, 계산식, 액션에서 사용할 수 있습니다.

지원 타입:

| 타입 | 설명 |
| --- | --- |
| `int32` | 32비트 정수 |
| `int64` | 64비트 정수 |
| `double` | 실수 |
| `string` | 문자열 |

별칭:

| 별칭 | 실제 타입 |
| --- | --- |
| `int` | `int32` |
| `long` | `int64` |
| `float`, `number` | `double` |
| `str`, `text` | `string` |

```python
scndui.variable("score", "int32", 0)
scndui.variable("player_name", "string", "Player")
```

### `var_text(variable_id)` / `var_ref(variable_id)`

텍스트에 넣을 변수 토큰을 반환합니다.

```python
scndui.variable("score", "int32", 0)
scndui.panel("score_panel", scndui.TL, 20, 20, 220, 40, "점수: " + scndui.var_text("score"))
```

직접 `{var:score}` 또는 `{score}` 형태를 써도 런처에서 해석됩니다. 문서에서는 `{var:score}` 형태를 권장합니다.

### `set_var(variable_id, value="", type_name="")`

변수값을 대입하는 액션을 만듭니다.

```python
scndui.action("reset_score", scndui.set_var("score", 0))
```

숫자 변수에는 계산식을 넣을 수 있습니다. 변수 토큰은 먼저 해석되고, 그 뒤 `+`, `-`, `*`, `/`, `%`, 괄호가 계산됩니다.

```python
scndui.variable("a", "int32", 10)
scndui.variable("b", "int32", 20)
scndui.variable("total", "int32", 0)

scndui.action("calc", scndui.set_var("total", "({var:a} + {var:b}) * 2"))
```

### `add_var(variable_id, value=1)` / `sub_var(variable_id, value=1)`

숫자 변수에 값을 더하거나 뺍니다.

```python
scndui.action("plus_5", scndui.add_var("score", 5))
scndui.action("minus_5", scndui.sub_var("score", 5))
```

### `inc_var(variable_id)` / `dec_var(variable_id)`

숫자 변수를 1 증가/감소합니다.

```python
scndui.action("plus_one", scndui.inc_var("score"))
scndui.action("minus_one", scndui.dec_var("score"))
```

### `action_variable(variable_id, value="", op="set", type_name="")`

변수 액션의 원형 함수입니다. 보통은 `set_var`, `add_var`, `sub_var`, `inc_var`, `dec_var`를 쓰는 편이 안전합니다.

```python
scndui.action("set_name", scndui.action_variable("player_name", "Marine", "set", "string"))
```

## 페이지 전환

페이지 전환은 한 부모 안에서 “현재 보여줄 페이지 하나”를 관리하는 용도입니다.

### `page_panel(parent_id, page_id, anchor=TL, x=0, y=0, width=160, height=48, text="", active=0)`

부모 안에 페이지 패널을 만들고, 해당 부모의 페이지 그룹으로 등록합니다.

`active=1`이면 최초 표시 페이지입니다. `active=0`이면 최초 숨김입니다.

```python
scndui.panel("book", scndui.CENTER, 0, 0, 600, 420, "")

scndui.page_panel("book", "page_1", scndui.TL, 0, 0, 600, 420, "1페이지", active=1)
scndui.page_panel("book", "page_2", scndui.TL, 0, 0, 600, 420, "2페이지", active=0)
```

### `page_host(element_id, parent_id)`

이미 만든 패널을 특정 부모의 페이지로 등록합니다.

```python
scndui.panel_to("book", "page_custom", scndui.TL, 0, 0, 600, 420, "커스텀 페이지")
scndui.page_host("page_custom", "book")
scndui.hidden("page_custom")
```

### `page(scope_id, page_id)` / `show_page(scope_id, page_id)` / `action_page(scope_id, page_id)`

지정한 페이지 그룹에서 특정 페이지를 보여주는 액션입니다.

`scope_id`, `page_id`에는 `:`, `,`, `|`, `;`, 줄바꿈을 넣으면 안 됩니다.

```python
scndui.action("next", scndui.page("book", "page_2"))
```

### `page_switch(button_id, scope_id, page_id)`

버튼에 페이지 전환 액션을 바로 연결합니다.

```python
scndui.button_to("page_1", "next", scndui.BR, 16, 16, 80, 36, "다음")
scndui.page_switch("next", "book", "page_2")
```

## 핫키

### `hotkey(element_id, key, action="")`

요소에 키 액션을 연결합니다. 키는 스타크래프트에도 계속 전달됩니다.

```python
scndui.hotkey("menu_panel", "F8", scndui.toggle("menu_panel"))
```

### `hotkey_toggle(element_id, key, start_visible=1)`

키로 해당 요소를 토글합니다.

```python
scndui.hotkey_toggle("menu_panel", "F8", start_visible=0)
```

### `key_action(element_id, key, value)`

`hotkey()`의 별칭입니다.

```python
scndui.key_action("menu_panel", "F8", scndui.show("menu_panel"))
```

### `key_toggle(element_id, key, start_visible=1)`

`hotkey_toggle()`의 별칭입니다.

### `press_hotkey(element_id, key)` / `press_key(element_id, key)`

버튼이 보이는 동안 키를 누르면 그 버튼의 액션을 실행합니다.

```python
scndui.button("confirm", scndui.CENTER, 0, 200, 120, 36, "확인")
scndui.action("confirm", scndui.hide("confirm"))
scndui.press_hotkey("confirm", "Enter")
```

## 문서 생성과 출력

### `set_base_size(base_width=1600, base_height=1200)`

좌표계 기준 크기를 설정합니다.

```python
scndui.set_base_size(1600, 1200)
```

### `build(panels=None, buttons=None, inputs=None, images=None, variables=None, base_width=1600, base_height=1200)`

딕셔너리 기반으로 `uiux_init` 문서를 직접 만듭니다.

```python
doc = scndui.build(
    panels=[scndui.panel_def("root", scndui.CENTER, 0, 0, 500, 300, "루트")],
    variables=[{"i": "score", "t": "int32", "v": 0}],
)
```

### `write_uiux_init(doc=None, panels=None, buttons=None, inputs=None, images=None, **kwargs)`

직접 만든 문서를 `uiux_init.json`으로 MPQ에 기록합니다. 문서의 이미지 에셋도 함께 추가합니다.

```python
doc = scndui.build(panels=[scndui.panel_def("root", scndui.CENTER, 0, 0, 500, 300, "루트")])
scndui.write_uiux_init(doc)
```

### `write()`

현재까지 `panel()`, `button()`, `add_image()`, `variable()` 등으로 쌓은 문서를 출력합니다.

```python
scndui.write()
```

## 제작 패턴 예시

### 메뉴와 안내창

```python
import scndui

scndui.button("menu", scndui.BL, 40, 35, 120, 36, "메뉴")
scndui.theme("menu", "pass")
scndui.action("menu", scndui.toggle("menu_panel"))

scndui.panel("menu_panel", scndui.T, 20, -266, 155, 251, "")
scndui.theme("menu_panel", "success")
scndui.pass_click("menu_panel")
scndui.reference("menu_panel", "menu")
scndui.hidden("menu_panel")
scndui.appear("menu_panel", "slide", 200, "up")

scndui.button_to("menu_panel", "play", scndui.T, 0, 15, 120, 36, "플레이 방법")
scndui.action(
    "play",
    scndui.actions(
        scndui.show("guide_panel"),
        scndui.content("guide_panel", "디오펜스는 아래 비콘을 통해 오펜스를 한다.\n디펜스를 통해 잘 막으면 된다."),
    ),
)

scndui.panel("guide_panel", scndui.CENTER, 0, 0, 800, 600, "설명란")
scndui.theme("guide_panel", "panel")
scndui.hidden("guide_panel")

scndui.button_to("guide_panel", "guide_close", scndui.TR, 5, 5, 50, 50, "X")
scndui.action("guide_close", scndui.hide("guide_panel"))

scndui.write()
```

### 입력값 검사

```python
import scndui

scndui.input_box("input_1", scndui.TL, 722, 491, 160, 34, "", "all", "여기에 코드입력")
scndui.panel("result_input", scndui.CENTER, 20, 106, 368, 170, "")

scndui.button("test_input_next", scndui.TL, 743, 550, 120, 36, "확인")
scndui.action(
    "test_input_next",
    scndui.if_match(
        "input_1",
        "1234",
        scndui.content("result_input", "코드가 일치합니다.", scndui.content_align("center")),
        scndui.elseif_match(
            "input_1",
            "12345",
            scndui.content("result_input", "2번째 코드를 찾으셨군요.", scndui.content_align("center")),
        ),
        scndui.else_do(
            scndui.content("result_input", "그런 코드는 없습니다.", scndui.content_align("center"))
        ),
    ),
)

scndui.write()
```

### 변수와 계산식

```python
import scndui

scndui.variable("kill", "int32", 0)
scndui.variable("bonus", "int32", 5)
scndui.variable("score", "int32", 0)

scndui.panel("score_panel", scndui.TL, 20, 20, 260, 48, "점수: {var:score}")
scndui.button("add_kill", scndui.TL, 20, 80, 160, 36, "킬 +1")

scndui.action(
    "add_kill",
    scndui.actions(
        scndui.add_var("kill", 1),
        scndui.set_var("score", "({var:kill} * 10) + {var:bonus}"),
        scndui.content("score_panel", "점수: {var:score}"),
    ),
)

scndui.write()
```

### 텍스트 안 이미지

```python
import scndui

scndui.add_image(4, "assets/gem.png")
scndui.panel("reward_panel", scndui.CENTER, 0, 0, 400, 120, "이미지 테스트 {img:4}")
scndui.content_image_on("reward_panel", 4, 0, "c", 24, 24, mode="text")

scndui.write()
```

## 주의사항

- 요소 ID는 가능하면 영문, 숫자, `_` 조합을 권장합니다.
- `page()`와 `show_only()`에 들어가는 ID에는 `:`, `,`, `|`, `;`, 줄바꿈을 쓰면 안 됩니다.
- 제작자 이미지 ID는 내부적으로 런처 이미지 영역과 충돌하지 않도록 오프셋 처리됩니다. 일반 제작자는 `add_image(1, "...")`처럼 자연 번호를 쓰면 됩니다.
- `{img:N}`을 텍스트처럼 쓰려면 해당 이미지가 문서에 등록되어 있어야 하고, `content_image(..., mode="text")` 또는 `content_image_on(..., mode="text")`가 함께 있어야 합니다.
- `{var:name}`은 런처 런타임에서 치환됩니다. 변수값이 또 다른 변수 토큰을 포함하면 최대 8단계까지 해석됩니다.
- `int32`, `int64`는 계산 결과를 정수로 자르고 범위에 맞게 제한합니다.
- `double` 계산식은 `+`, `-`, `*`, `/`, `%`, 괄호, 지수 표기 숫자를 지원합니다.
- 조건식 `if_expr()`에서는 각 원자를 반드시 `[]`로 감싸야 하며, `AND`가 `OR`보다 먼저 계산됩니다. 괄호로 우선순위를 바꿀 수 있습니다.
- 패널을 `pass_click()`으로 설정하면 마우스 클릭이 스타크래프트로 통과합니다. 모달처럼 클릭을 막아야 하는 패널은 `block_click()`을 사용하세요.

## 전체 공개 별칭

`f_` 접두 별칭은 EPScript 쪽에서 함수 이름 충돌을 줄이기 위해 제공됩니다. 예를 들어 `f_button`, `f_action`, `f_content`, `f_if_match`, `f_write`는 각각 `button`, `action`, `content`, `if_match`, `write`와 같습니다.

자주 쓰는 별칭:

| 별칭 | 실제 함수 |
| --- | --- |
| `f_log` | `log` |
| `f_toggle` | `toggle` |
| `f_show` | `show` |
| `f_hide` | `hide` |
| `f_show_only` | `show_only` |
| `f_page`, `f_show_page` | `page`, `show_page` |
| `f_content`, `f_content_rich` | `content`, `content_rich` |
| `f_style_action` | `style_action` |
| `f_rect_action` | `rect_action` |
| `f_if_match`, `f_if_ge`, `f_if_expr` | 조건 함수 |
| `f_variable`, `f_var`, `f_var_text` | 변수 함수 |
| `f_add_image`, `f_register_image` | 이미지 등록 |
| `f_background_image`, `f_content_image_on` | 이미지 배치 |
| `f_write` | `write` |
