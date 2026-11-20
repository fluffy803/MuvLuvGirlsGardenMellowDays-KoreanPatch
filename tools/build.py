# マブラヴ ガールズガーデン Mellow Days (정식판, Steam 5005700) 한국어 패치 빌드 (중국어 간체 칸 → 한국어)
#   python tools/build.py [게임 폴더] [출력 폴더] [--allow-missing]
#     게임 폴더: 패치 전 원본 (기본값: Steam 기본 설치 경로). meta/game.json 의 해시와 맞아야 한다
#     출력 폴더: 게임 폴더에 덮어쓸 파일이 만들어진다 (기본값: dist/stage)
#     --allow-missing: 번역이 없는 키가 있어도 빌드 (시험용. 빠진 줄은 중국어 원문이 남는다)
#   환경변수 MLD_FONT: Noto Sans KR 가변 폰트 ttf (기본값: Windows 기본 글꼴 NotoSansKR-VF.ttf, OFL)
#   환경변수 MLD_TRANSLATIONS: 번역 폴더(loc.ko.json·story.ko.json) 바꾸기 (기본값: translations)
#   필요: Python 3.10+, UnityPy, fonttools, .NET SDK 9 (tools/dllpatch)
#
# 1. DLL: 기본 언어 ja → zh-Hans (새로 설치하면 한국어로 시작). 화면 문구는 정식판에서 전부 번역표로 옮겨져 DLL 문구 패치 없음
# 2. 문자열 테이블: zh-Hans 7개 테이블 전체 한국어. ja/en/zh-Hant 테이블은 언어 메뉴의 "한국어" 라벨만
# 3. zh-Hans 에셋 번들: NotoSansSC-Light 원본 폰트 → 한글 폰트, 튜토리얼 이미지 → 영어판
# 4. shared 에셋 번들: 중국어 로고(zh-Hans·zh-Hant 공용) → 영어판 로고
# 5. resources.assets: TMP 전역 폴백 NotoSansSC-Light 원본 폰트 → 한글 폰트
# 6. catalog.bin: 수정한 번들 CRC → 0 (검사 끔), 크기 필드 갱신
import sys, os, io, json, struct, subprocess, tempfile, hashlib
import UnityPy
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
ALLOW_MISSING = "--allow-missing" in sys.argv
GAME = args[0] if len(args) > 0 else r"C:\Program Files (x86)\Steam\steamapps\common\Muv-Luv ガールズガーデン ～Mellow Days～"
OUT = args[1] if len(args) > 1 else os.path.join(REPO, "dist", "stage")
VF = os.environ.get("MLD_FONT") or os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "NotoSansKR-VF.ttf")

DATA = os.path.join(GAME, "MellowDays_Data")
AA = os.path.join(DATA, "StreamingAssets", "aa")
BUN = os.path.join(AA, "StandaloneWindows64")
O_DATA = os.path.join(OUT, "MellowDays_Data")
O_BUN = os.path.join(O_DATA, "StreamingAssets", "aa", "StandaloneWindows64")
O_MAN = os.path.join(O_DATA, "Managed")
for d in (O_BUN, O_MAN):
    os.makedirs(d, exist_ok=True)

# 원본 확인 (이미 패치된 파일이나 다른 빌드면 중단)
meta = json.load(open(os.path.join(REPO, "meta", "game.json"), encoding="utf-8"))
for f, h in meta["sha256"].items():
    if hashlib.sha256(open(os.path.join(GAME, f), "rb").read()).hexdigest() != h:
        sys.exit(f"원본과 다른 파일: {f} (패치가 이미 적용됐거나 게임이 업데이트됨 — Steam 무결성 검사로 원본을 받을 것)")

tr = {}
for f in ("loc.ko.json", "story.ko.json"):
    tr.update(json.load(open(os.path.join(os.environ.get("MLD_TRANSLATIONS") or os.path.join(REPO, "translations"), f), encoding="utf-8")))
print("번역", len(tr))


def B(name):
    return f"localization-{name}_assets_all.bundle"


ZH_STR, JA_STR, EN_STR, HANT_STR = (B("string-tables-chinese(simplified)(zh-hans)"), B("string-tables-japanese(ja)"),
                                    B("string-tables-english(en)"), B("string-tables-chinese(traditional)(zh-hant)"))
ZH_AST, EN_AST, SHARED = B("assets-chinese(simplified)(zh-hans)"), B("assets-english(en)"), B("assets-shared")

# ------------------------------------------------------------------ 1. DLL
rules = [dict(asm="Garden.Models.dll", type="Garden.GeneralSettings", old="ja", new="zh-Hans")]
with tempfile.TemporaryDirectory() as tmp:
    rp = os.path.join(tmp, "dll_rules.json")
    json.dump(rules, open(rp, "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run(["dotnet", "run", "--project", os.path.join(HERE, "dllpatch"), "-c", "Release", "-v", "q", "--",
                        os.path.join(DATA, "Managed"), O_MAN, rp], capture_output=True, text=True, encoding="utf-8")
print(r.stdout.strip())
if r.returncode:
    sys.exit("[오류] DLL 패치 실패\n" + r.stderr)


# ------------------------------------------------------------------ 2. 문자열 테이블
KEYS = {}   # 테이블 → {m_Id: 키}  (키 끝에 탭·줄바꿈이 붙은 것도 있어 SharedTableData 값을 그대로 쓴다)
shared_env = UnityPy.load(os.path.join(BUN, SHARED))
for obj in shared_env.objects:
    if obj.type.name == "MonoBehaviour":
        tree = obj.read_typetree()
        if tree.get("m_Name", "").endswith(" Shared Data") and "m_Entries" in tree:
            KEYS[tree["m_Name"][:-len(" Shared Data")]] = {e["m_Id"]: e["m_Key"] for e in tree["m_Entries"]}


def patch_tables(bundle, values, missing=None):
    """values: {(테이블, 키): 문자열}"""
    env = UnityPy.load(os.path.join(BUN, bundle))
    n = 0
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        tree = obj.read_typetree()
        table = tree["m_Name"].rsplit("_", 1)[0]
        keys = KEYS[table]
        changed = False
        for e in tree["m_TableData"]:
            k = keys.get(e["m_Id"])
            v = values.get((table, k))
            if v is not None:
                e["m_Localized"] = v
                n += 1
                changed = True
            elif missing is not None and e["m_Localized"]:
                missing.append(f"{table}/{k}")
        if changed:
            obj.save_typetree(tree)
    open(os.path.join(O_BUN, bundle), "wb").write(env.file.save(packer="original"))
    return n


zh_vals = {tuple(k.split("/", 1)): v for k, v in tr.items()}
missing = []
n = patch_tables(ZH_STR, zh_vals, missing)
print(f"zh-Hans 문자열 테이블: {n}/{len(zh_vals)} 항목 교체")
assert n == len(zh_vals), "테이블에 없는 키가 있음"
if missing:
    print(f"[경고] 번역 없는 키 {len(missing)}개: {missing[:10]}")
    if not ALLOW_MISSING:
        sys.exit("번역이 빠진 키가 있어 중단 (시험 빌드는 --allow-missing)")
label = tr["Common/Setting-Language-zhHans"]
for b in (JA_STR, EN_STR, HANT_STR):
    n = patch_tables(b, {("Common", "Setting-Language-zhHans"): label})
    print(f"{b}: 언어 메뉴 라벨 {n}개")
    assert n == 1

# ------------------------------------------------------------------ 3. 한글 폰트 (Noto Sans KR Light)
f = TTFont(VF, recalcTimestamp=False)   # 저장 시각을 남기지 않아 빌드마다 결과가 같다
inst = instancer.instantiateVariableFont(f, {"wght": 300}, updateFontNames=False)
# 세로 메트릭을 원본 NotoSansSC-Light(hhea 1160/-288)에 맞춤 — TMP FaceInfo는 원본 SC 기준으로 저장돼 있음
inst["hhea"].ascent, inst["hhea"].descent, inst["hhea"].lineGap = 1160, -288, 0
nm = inst["name"]
for rec in list(nm.names):
    if rec.nameID in (1, 4):
        nm.setName("Noto Sans KR Light", rec.nameID, rec.platformID, rec.platEncID, rec.langID)
    if rec.nameID == 6:
        nm.setName("NotoSansKR-Light", rec.nameID, rec.platformID, rec.platEncID, rec.langID)
    if rec.nameID in (16, 17, 21, 22):
        nm.removeNames(nameID=rec.nameID)
inst["OS/2"].usWeightClass = 300
buf = io.BytesIO()
inst.save(buf)
kfont = buf.getvalue()


# ------------------------------------------------------------------ 4. 이미지·폰트
def copy_texture(d, s):
    """d(대상 Texture2D) 픽셀을 s(영어판)로. 크기·밉맵이 같아야 하고, 압축 형식은 영어판 것을 따른다."""
    assert (s.m_Width, s.m_Height, s.m_MipCount) == (d.m_Width, d.m_Height, d.m_MipCount), d.m_Name
    raw = s.get_image_data()
    d.m_TextureFormat = s.m_TextureFormat
    d.image_data = raw
    d.m_CompleteImageSize = len(raw)
    d.m_StreamData.offset, d.m_StreamData.size, d.m_StreamData.path = 0, 0, ""
    d.save()


en_tex = {}
for obj in UnityPy.load(os.path.join(BUN, EN_AST)).objects:
    if obj.type.name == "Texture2D":
        d = obj.read()
        en_tex[d.m_Name.rsplit("_", 1)[0]] = d

env = UnityPy.load(os.path.join(BUN, ZH_AST))
done = []
for obj in env.objects:
    if obj.type.name == "Font":
        d = obj.read()
        if d.m_Name == "NotoSansSC-Light":
            d.m_FontData = kfont
            d.save()
            done.append("font")
    elif obj.type.name == "Texture2D":
        d = obj.read()
        base = d.m_Name.rsplit("_", 1)[0]
        if d.m_Name.endswith("_zh-Hans") and base in en_tex:
            copy_texture(d, en_tex[base])
            done.append(base)
open(os.path.join(O_BUN, ZH_AST), "wb").write(env.file.save(packer="original"))
print("zh-Hans 에셋 번들:", done)
assert "font" in done and len(done) == 6

done = []
for obj in shared_env.objects:
    if obj.type.name == "Texture2D":
        d = obj.read()
        if d.m_Name == "logo_zh-Hans":
            copy_texture(d, en_tex["logo"])
            done.append(d.m_Name)
open(os.path.join(O_BUN, SHARED), "wb").write(shared_env.file.save(packer="original"))
print("shared 에셋 번들:", done)
assert done == ["logo_zh-Hans"]

# ------------------------------------------------------------------ 5. resources.assets 폴백 폰트
env = UnityPy.load(os.path.join(DATA, "resources.assets"))
hit = 0
for obj in env.objects:
    if obj.type.name == "Font":
        d = obj.read()
        if d.m_Name == "NotoSansSC-Light":
            d.m_FontData = kfont
            d.save()
            hit += 1
assert hit == 1
sf = list(env.files.values())[0]
open(os.path.join(O_DATA, "resources.assets"), "wb").write(sf.save())
print("resources.assets: 폴백 폰트 교체")

# ------------------------------------------------------------------ 6. catalog.bin CRC
cat = bytearray(open(os.path.join(AA, "catalog.bin"), "rb").read())
CRC = {ZH_STR: (0x6894, 0xEA14718F), JA_STR: (0x6CB0, 0xFE433AFB), EN_STR: (0x6B5C, 0x85CF9931), HANT_STR: (0x6A0A, 0x6444DE1C),
       ZH_AST: (0x5B3E, 0xD7CC4D1A), SHARED: (0x606A, 0xB2D6C01D)}
for b, (off, old) in CRC.items():
    cur_crc, cur_size = struct.unpack_from("<II", cat, off)
    assert cur_crc == old and cur_size == os.path.getsize(os.path.join(BUN, b)), f"카탈로그 위치가 다름(게임 업데이트?): {b}"
    struct.pack_into("<II", cat, off, 0, os.path.getsize(os.path.join(O_BUN, b)))
open(os.path.join(O_DATA, "StreamingAssets", "aa", "catalog.bin"), "wb").write(cat)
print("catalog.bin: CRC 0 처리", len(CRC), "개")

for root, _, files in os.walk(O_DATA):
    for fn in files:
        p = os.path.join(root, fn)
        print(f"  {os.path.relpath(p, OUT)}  {os.path.getsize(p):,}")
