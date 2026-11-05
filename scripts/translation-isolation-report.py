"""Summarize paired local MT results; semantic ratings are explicit, not an oracle."""

import argparse
import html
import json
import statistics
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("output", type=Path)
args = parser.parse_args()
p = args.output


def load(name):
    return json.loads((p / name).read_text())


old = {r["id"]: r for r in load("regression-results.json") + load("synthetic-results.json")}
rows = []
for row in load("final-regression-results.json"):
    rows.append(
        dict(
            row,
            methods={"old": old[row["id"]]["methods"]["old"], "new": row["methods"]["production"]},
        )
    )
for row in load("final-heldout-results.json"):
    # The second heldout range reuses H01...H21; qualify evidence identifiers.
    row = dict(row, id=("V2-" if row["group"] == "heldout" else "") + row["id"])
    rows.append(
        dict(row, methods={"old": row["methods"]["old"], "new": row["methods"]["production"]})
    )
ratings = load("ratings.json")
assert set(ratings) == {r["id"] for r in rows}
summary = {}
for group in sorted({r["group"] for r in rows}):
    selected = [r for r in rows if r["group"] == group]
    summary[group] = {"rows": len(selected)}
    for method in ["old", "new"]:
        summary[group][method] = {
            "clear_neighbor_import": sum(ratings[r["id"]][method] for r in selected),
            "truncated": sum(r["methods"][method].get("truncated", False) for r in selected),
            "total_compute_seconds": sum(r["methods"][method]["wall_seconds"] for r in selected),
            "median_compute_seconds": statistics.median(
                r["methods"][method]["wall_seconds"] for r in selected
            ),
        }
summary["assessment"] = (
    "Assistant review against fixed input text, not human audio truth. "
    "Counts independent neighbour propositions, not all semantic errors. "
    "E-hard4 duplicates E-base-00; do not pool groups as independent samples."
)
(p / "paired.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
(p / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))


def esc(v):
    return html.escape(str(v))


parts = [
    '<!doctype html><meta charset="utf-8"><title>中文回改目标隔离对照</title>',
    "<style>body{font:16px/1.6 system-ui;max-width:1100px;margin:30px auto;padding:20px}"
    "pre{white-space:pre-wrap}details{border:1px solid #ccc;padding:12px;margin:12px 0}"
    "summary{cursor:pointer}</style>",
    "<h1>中文回改：固定输入的修复前后对照</h1>",
    "<p>旧／新均为同一本地模型。这里只核对中文是否忠实于固定英文，不把ASR当原音真值。已知反例为开发集，V2为第二轮冻结后的留出。短句补全、词义错误另列，不能以串段计数代表整体准确率。</p>",
]
for row in rows:
    rating = ratings[row["id"]]
    parts.append(
        f'<details id="{esc(row["id"])}"><summary>{esc(row["id"])} · {esc(row["group"])}</summary>'
    )
    parts.append(
        f"<p>{esc(rating['note'])}</p><pre>当前输入：{esc(row['text'])}\n前文：{esc(row['context'])}\n后文：{esc(row['following'])}</pre>"
    )
    for method in ["old", "new"]:
        value = row["methods"][method]
        parts.append(
            f"<h3>{method}</h3><pre>{esc(value['text'])}</pre>"
            f"<p>截断：{esc(value.get('truncated', False))}；"
            f"计算：{value['wall_seconds']:.3f}秒</p>"
        )
    if row["id"].startswith("V2-"):
        start = row["start"] - 1104
        parts.append(
            '<audio controls preload="none" '
            f'src="holdout2-context.wav#t={start},{start + 12}"></audio>'
        )
    parts.append("</details>")
parts.append("<h2>首轮失败与残余问题（不隐藏）</h2>")
first = {r["id"]: r for r in load("regression-results.json")}
retired = {r["id"]: r for r in load("heldout-results.json")}
round2 = {r["id"]: r for r in load("dev-round2-results.json")}
for key, previous in [("R-base-23", first["R-base-23"]), ("H03", retired["H03"])]:
    parts.append(f"<details><summary>首轮 {key}</summary>")
    parts.append(
        f"<pre>输入：{esc(previous['text'])}\n"
        f"旧提示：{esc(previous['methods']['old']['text'])}\n"
        f"首轮：{esc(previous['methods']['production']['text'])}\n"
        f"最终严格提示：{esc(round2[key]['methods']['strict']['text'])}</pre></details>"
    )
parts.append('<p><a href="../classroom-baseline-20261105/index.html">此前课堂原始回听页</a></p>')
(p / "index.html").write_text("".join(parts))
print(json.dumps(summary, ensure_ascii=False, indent=2))
