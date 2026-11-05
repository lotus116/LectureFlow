"""Build private, auditable classroom evidence. Counts changes, never invents truth."""

import argparse
import collections
import hashlib
import html
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
out = args.output
manifest = json.loads((out / "manifest.json").read_text())
summary = {
    "baseline": {},
    "coverage": {},
    "methods": {},
    "hint_pairs": {},
    "quality_gate": "limited_no_audio_verified_reference",
    "verified_corrections": None,
    "classroom_wer": None,
}
html_rows = []
case_notes = {
    "E-hard4": (
        "可核实的基线翻译越界：A-text初译限定当前目标；回改加入后"
        "两段中的请求。无需先认定英文听写正确，也能核对译文超出输入目标。"
    ),
    "R-1": (
        "两项独立风险：C提示使专家报告候选转向出口报告；B0初译仍对应当前"
        "目标，回改却翻译后两段。前者的原音词义待听核，后者可由输入范围核实。"
    ),
    "R-6": (
        "B0初译对应当前目标，最终回改改为后两段内容；属于翻译目标隔离失效。"
        "原音动作方向仍待听核。"
    ),
    "R-protect1": (
        "回改有邻段污染迹象，但窗口文本投影遗漏末尾闭引号，"
        "不能排除研究投影对翻译的影响，不作主要无条件证据。"
    ),
    "R-protect2": (
        "长原音产生更符合领域的术语候选；只属有依据待听核。"
        "保护候选是机械筛选，不等于已知正确样本。"
    ),
    "R-5": (
        "数字、单位、否定和字幕边界在候选间变化；不能凭流畅度选择。"
        "带后文的投影可能包含下一字幕，先核对原窗口。"
    ),
    "E-2": "同一目标在候选间出现相反的网络连接动作方向；无逐字参考，不能确定赢家。",
    "E-4": "长原音得到更连贯的银行说明语义，是待听核收益候选；不能据此认定已纠正。",
    "E-hard3": (
        "C与A-batch同原音，仅增加资料提示；C出现连续18次h"
        "ow，而无提示版本无此重复。可核实结构退化，不需猜测实际发音。"
    ),
}
baseline_rows = []


def words(t):
    return re.findall(r"[a-z]+(?:'[a-z]+)?|\d+", t.lower())


def esc(t):
    return html.escape(str(t))


for record in manifest["recordings"]:
    label = record["id"]
    base = json.loads((out / f"{label}-baseline.json").read_text())
    rows = json.loads((out / f"{label}-ablation.json").read_text())
    picks = json.loads((out / f"{label}-selection.json").read_text())
    assert {r["id"] for r in rows} == {p["id"] for p in picks}
    summary["baseline"][label] = {
        k: v for k, v in base.items() if k not in ["events", "final", "first_stable"]
    }
    summary["coverage"][label] = dict(collections.Counter(r["kind"] for r in rows))
    summary["baseline"][label]["stable_captions"] = len(base["first_stable"])
    summary["baseline"][label]["drain_seconds"] = base["wall_seconds"] - base["seconds"]
    summary["baseline"][label]["retained_audio_seconds"] = sum(
        e["end"] - e["start"] for e in base["final"]
    )
    baseline_rows.append(f"<h2>{esc(label)} · 300秒完整基线</h2>")
    for e in base["final"]:
        audio = Path(record["audio"]).as_uri() + f"#t={e['start'] + 30},{e['end'] + 30}"
        baseline_rows.append(
            f'<details><summary>源 {record["start"] + e["start"]:.2f}–{record["start"] + e["end"]:.2f} 秒</summary><audio controls preload="none" src="{esc(audio)}"></audio><pre>{esc(e["english"])}\n{esc(e["chinese"])}</pre></details>'  # noqa: E501
        )
    for row in rows:
        e = row["target"]
        original = e["english"]
        methods = row["methods"]
        assert set(["A_text", "A_batch", "B0", "B5", "C", "D0", "D5"]) <= set(methods)
        for name, r in methods.items():
            key = row["kind"] + ":" + name
            s = summary["methods"].setdefault(key, collections.Counter())
            s.update(
                samples=1,
                english_changed=int(words(r["projection"]["text"]) != words(original)),
                projection_uncertain=int(bool(r["projection"]["uncertain"])),
                truncated=int(r["truncated"]),
                initial_mt_truncated=int(r["initial_chinese_truncated"]),
                final_mt_truncated=int(r["final_chinese_truncated"]),
                actual_asr_seconds=0 if r.get("reused") else r["seconds"],
                mt_seconds=r["mt_seconds"],
                final_chinese_changed=int(r["final_chinese"] != methods["A_text"]["final_chinese"]),
            )
        for hinted, control in [("C", "A_batch"), ("D0", "B0"), ("D5", "B5"), ("U", "A_batch")]:
            if hinted not in methods:
                continue
            x, y = methods[hinted], methods[control]
            key = row["kind"] + ":" + hinted
            s = summary["hint_pairs"].setdefault(key, collections.Counter())
            s.update(
                samples=1,
                nonempty_hint=int(bool(x["hint"])),
                english_changed=int(
                    words(x["projection"]["text"]) != words(y["projection"]["text"])
                ),
                raw_window_changed=int(words(x["raw"]) != words(y["raw"])),
            )
        fixed = next((p.get("core") for p in picks if p["id"] == row["id"]), None)
        start = fixed["start"] if fixed else max(0, e["start"] - 6)
        end = fixed["end"] if fixed else min(300, start + 25)
        audio = Path(record["audio"]).as_uri() + f"#t={start + 30},{end + 30}"
        html_rows.append(
            f'<details id="{esc(row["id"])}"><summary>{esc(row["id"])} · {esc(row["kind"])} · 源 {record["start"] + e["start"]:.2f}–{record["start"] + e["end"]:.2f} 秒</summary><p>{esc(case_notes.get(row["id"], "未单独作语义判定；保留全部方法便于核查。"))}</p><p>核心回听范围 {record["start"] + start:.2f}–{record["start"] + end:.2f} 秒。语音词义尚待听核，课件匹配不是逐字真值。</p><audio controls preload="none" src="{esc(audio)}"></audio><h3>真实基线</h3><pre>{esc(original)}\n{esc(e["chinese"])}</pre><h3>相同翻译上下文</h3><pre>前两段：{esc(row["context"])}\n后两段：{esc(row["following"])}</pre><h3>自动资料提示</h3><pre>{esc(row["hint"]["text"])}</pre>'  # noqa: E501
        )
        for p in row["hint"]["pages"]:
            html_rows.append(
                f"<p>{esc(p['name'])} · p.{esc(p['page'])} · 自动检索分数 {p['score']}</p><details><summary>页文字证据</summary><pre>{esc(p['text'])}</pre></details>"  # noqa: E501
            )
        for name, r in methods.items():
            html_rows.append(
                f"<h3>{esc(name)} · 原音 {record['start'] + r['start']:.2f}–{record['start'] + r['end']:.2f}秒</h3><p>目标投影不确定性：{esc(r['projection']['uncertain'])}；截断：{r['truncated']}</p><pre>原窗口：{esc(r['raw'])}\n目标投影：{esc(r['projection']['text'])}\n初译：{esc(r['initial_chinese'])}\n回改：{esc(r['final_chinese'])}</pre>"  # noqa: E501
            )
        html_rows.append("</details>")
checks = {}
for path, meta in manifest["hashes_before"].items():
    checks[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest() == meta["sha256"]
assert all(checks.values())
summary["source_hashes"] = {"files": len(checks), "unchanged": all(checks.values())}
(out / "hash-verification.json").write_text(json.dumps(checks, indent=2))
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
page = (
    """<!doctype html><html lang="zh"><meta charset="utf-8"><title>真实课堂基线与候选证据</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:30px auto;padding:0 20px;background:#f5f7fa;color:#142536}details{background:white;border:1px solid #ccd6df;border-radius:8px;margin:15px 0;padding:14px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere}audio{width:100%}</style><h1>真实课堂基线与有限对照</h1><p>12个预定核心＋单列诊断／保护候选。A-text仅固定英文重译，不是正确参考。B0/D0无未来语音；B5/D5最多等待5秒后文。回改中文另使用相同后两段上下文，不能称5秒内完成。所有词义纠正待听核，不计算WER。</p>"""  # noqa: E501
    + "".join(html_rows)
    + "<h1>完整英语基线</h1>"
    + "".join(baseline_rows)
    + '<p><a href="mixed-pilot.html">独立混合语言先导（不计入英语分母）</a></p>'
    + "</html>"
)
(out / "index.html").write_text(page)
pilot = json.loads((out / "N-baseline-with-retrieval-probe.json").read_text())
pilot_html = [
    '<!doctype html><meta charset="utf-8"><h1>混合语言先导：不计入英语主实验</h1>',
    "<p>源300–600秒；25字幕中19条识别文本含中文；CPU检索预检查干扰过计时。只保留诊断证据，不作主性能基线。72秒及180秒目标回改包含内部提示标记。</p>",
]
for e in pilot["final"]:
    audio = (out / "N-context.wav").resolve().as_uri() + f"#t={e['start'] + 30},{e['end'] + 30}"
    pilot_html.append(
        f'<details><summary>源 {300 + e["start"]:.2f}–{300 + e["end"]:.2f} 秒</summary><audio controls preload="none" src="{esc(audio)}"></audio><pre style="white-space:pre-wrap">{esc(e["english"])}\n{esc(e["chinese"])}</pre></details>'  # noqa: E501
    )
(out / "mixed-pilot.html").write_text("".join(pilot_html))
print(json.dumps(summary, ensure_ascii=False, indent=2))
