"""Тепловая карта рисков 5×3 (вероятность × влияние) для отчёта — PNG через matplotlib."""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from app.template_engine.expr import zone_for  # noqa: E402

ZONE_COLORS = {"red": "#ef9a9a", "yellow": "#fff59d", "green": "#a5d6a7"}


def heatmap_png(
    zones: dict[str, str],
    probability_scale: dict[int, str],
    impact_scale: dict[int, str],
    risks: list[tuple[str, int, int]],  # (код, вероятность, влияние)
    font: str = "DejaVu Serif",
) -> bytes:
    ps, iss = sorted(probability_scale), sorted(impact_scale)
    fig, ax = plt.subplots(figsize=(6.3, 4.6), dpi=200)
    plt.rcParams["font.family"] = [font, "DejaVu Serif"]
    for col, i in enumerate(iss):
        for row, p in enumerate(ps):
            ax.add_patch(
                plt.Rectangle(
                    (col, row), 1, 1, facecolor=ZONE_COLORS[zone_for(zones, p, i)], edgecolor="#555"
                )
            )
            codes = [c for c, rp, ri in risks if rp == p and ri == i]
            if codes:
                text = "\n".join(", ".join(codes[k : k + 4]) for k in range(0, len(codes), 4))
                ax.text(col + 0.5, row + 0.5, text, ha="center", va="center", fontsize=8)
    ax.set_xlim(0, len(iss))
    ax.set_ylim(0, len(ps))
    ax.set_xticks([k + 0.5 for k in range(len(iss))], [impact_scale[i] for i in iss], fontsize=8)
    ax.set_yticks([k + 0.5 for k in range(len(ps))], [probability_scale[p] for p in ps], fontsize=8)
    ax.set_xlabel("Масштаб влияния", fontsize=9)
    ax.set_ylabel("Вероятность", fontsize=9)
    ax.tick_params(length=0)
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format="png")
    plt.close(fig)
    return out.getvalue()
