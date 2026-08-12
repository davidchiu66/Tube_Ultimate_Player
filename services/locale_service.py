"""本地系统语言探测与音轨语言码匹配（R9 音轨选择的 D 裁定）。

按用户裁定，默认音轨优先跟随本地系统语言（中文系统优先中文、英文系统优先英文）。
这里封装两件事：

1. 把 `QLocale.system()` 收敛成一个 BCP-47 风格标签（`zh-Hans` / `en-US`），
   供音轨默认选择与设置页展示。
2. 把该标签与一组音轨语言码做四级由严到宽的匹配，保证"简繁不误配"。

为什么不用 `QLocale.name()`：它返回 `zh_CN` —— 带地区、不带书写体系；而
YouTube 的音轨语言码是 `zh-Hans` / `zh-Hant` —— 带书写体系、不带地区。直接比
字符串两边都匹配不上。`language()` + `script()` 组合才能对齐。

`system_language_tag(locale=...)` 的 `locale` 参数可显式注入（与
`platform_support.py`、`app_paths.py` 的既有可注入风格一致），让离线测试可以
构造任意区域设置的系统，不依赖跑测机器的真实区域。
"""

from __future__ import annotations

from PySide6.QtCore import QLocale

# 这些书写体系与语言一一对应，写不写都只有一个含义，属"隐含书写体系"。组装标签时
# 省掉它们，得到 en、ja 这种更短的形态——YouTube 的音轨语言码就是这样写的，
# 带上 Latn/Jpan 反而匹配不上。
_IMPLICIT_SCRIPTS = {"latn", "jpan", "kore", "hang", "hani", "hira", "kana"}

# 语言存在多套书写体系时，仅靠语言码无法确定用哪套，需保留 script 参与区分。
# 只列本项目实际会遇到的简繁中文；必要时再扩充。
_MULTI_SCRIPT_LANGUAGES = {"zh"}

# 语言码里的"中性书写体系"：不带书写体系的码表示该语言的默认书写体系。
# zh 中性码 = 简体，与汉字默认书写体系一致。
_NEUTRAL_SCRIPTS = {"zh": "hans"}


def system_language_tag(locale: QLocale | None = None) -> str:
    """返回系统语言的 BCP-47 风格标签，如 zh-Hans-CN、en-US、ja-JP。

    - 语言只有一套书写体系时省略 script（en-US 而非 en-Latn-US）；
    - 语言有多套书写体系（目前只有中文）时带上 script（zh-Hans-CN / zh-Hant-TW）；
    - 保留地区。地区在匹配音轨时会被忽略（第 2 级规则），但对设置页展示有意义。
    """
    if locale is None:
        locale = QLocale.system()
    lang_code = QLocale.languageToCode(locale.language())
    if not lang_code:
        return ""

    tag = lang_code
    script_code = QLocale.scriptToCode(locale.script())
    if (
        lang_code.lower() in _MULTI_SCRIPT_LANGUAGES
        and script_code
        and script_code.lower() not in _IMPLICIT_SCRIPTS
    ):
        tag += f"-{script_code}"
    territory_code = QLocale.territoryToCode(locale.territory())
    if territory_code:
        tag += f"-{territory_code}"
    return tag


def parse_language_tag(tag: str) -> tuple[str, str]:
    """把语言标签拆成 (语言, 书写体系)，都转小写；地区一律丢弃。

    书写体系按 BCP-47 的形状识别——4 个字母的子标签才是 script，`en-US` 的 `US`
    是地区不是书写体系。不做这层区分就会把 `US` 当成书写体系，导致英文系统
    永远匹配不上任何英文音轨。
    """
    parts = [part for part in str(tag or "").replace("_", "-").split("-") if part]
    if not parts:
        return "", ""
    language = parts[0].lower()
    script = ""
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            script = part.lower()
            break
    return language, script


def match_audio_language(languages: list[str], system_tag: str) -> str:
    """从候选音轨语言码里挑一条与系统语言最匹配的；挑不中返回空串。

    四级规则由严到宽：

    1. 完整标签相等（大小写与分隔符无关）—— zh-Hans 系统命中 zh-Hans。
    2. 语言 + 书写体系相等（忽略地区）—— zh-Hans-CN 系统命中 zh-Hans；
       en-GB 系统命中 en-US。
    3. 语言相等且书写体系不冲突，中性码优先—— 简中系统在 zh-Hant 与 zh 之间
       命中 zh（宁可取中性的简体默认，也不要繁体）。
    4. 语言相等（书写体系冲突时的最后兜底）—— 简中系统只有 zh-Hant 时仍命中
       zh-Hant：同语系的繁体远好过完全另一种语言。
    """
    sys_language, sys_script = parse_language_tag(system_tag)
    if not sys_language:
        return ""

    candidates = [str(code or "").strip() for code in languages]
    candidates = [code for code in candidates if code]

    def _key(code: str) -> str:
        return code.replace("_", "-").lower()

    # 第 1 级：完整标签相等。
    normalized_system = str(system_tag).replace("_", "-").lower()
    for code in candidates:
        if _key(code) == normalized_system:
            return code

    same_language = [code for code in candidates if parse_language_tag(code)[0] == sys_language]
    if not same_language:
        return ""

    # 第 2 级：语言 + 书写体系相等（地区可不同）。
    for code in same_language:
        if parse_language_tag(code)[1] == sys_script:
            return code

    # 第 3 级：书写体系不冲突时优先中性码（zh 而非 zh-Hant）。
    if _NEUTRAL_SCRIPTS.get(sys_language) == sys_script:
        for code in same_language:
            if not parse_language_tag(code)[1]:
                return code

    # 第 4 级：同语系兜底，取出现顺序最靠前的一条。
    return same_language[0]
