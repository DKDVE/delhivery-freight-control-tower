"""Shared plotting helpers (team palette, white background, no junk)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RED = "#EF4136"
DARK_RED = "#C21F26"
INK = "#14202B"
GREY = "#5B6470"
TEAL = "#1F6F78"
NAVY = "#1F3A5F"

PALETTE = [RED, DARK_RED, TEAL, NAVY, GREY]


def style(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor("white")
    if title:
        ax.set_title(title, color=INK, fontsize=12, fontweight="bold", loc="left")
    if xlabel:
        ax.set_xlabel(xlabel, color=GREY, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=GREY, fontsize=9)
    ax.tick_params(colors=GREY)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(GREY)
    ax.spines["bottom"].set_color(GREY)


def save(fig, path, dpi=110):
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)


def bar_chart(values, labels, path, title=None, xlabel=None, ylabel=None, color=TEAL, horizontal=True):
    fig, ax = plt.subplots(figsize=(7, 5))
    if horizontal:
        ax.barh(labels, values, color=color, height=0.7)
        ax.invert_yaxis()
    else:
        ax.bar(labels, values, color=color, width=0.7)
    style(ax, title=title, xlabel=xlabel, ylabel=ylabel)
    if horizontal:
        ax.tick_params(axis="y", labelsize=8)
    else:
        ax.tick_params(axis="x", rotation=30, labelsize=8)
    save(fig, path)
    return path


def beeswarm(shap_values, features, path, title=None):
    import shap
    fig, ax = plt.subplots(figsize=(8, 6))
    shap.plots.beeswarm(shap_values, features=features, show=False, max_display=15, plot_size=None)
    style(ax, title=title)
    save(fig, path)
    return path


def waterfall(shap_values, features, path, title=None):
    import shap
    fig, ax = plt.subplots(figsize=(8, 6))
    shap.plots.waterfall(shap_values[0], show=False, max_display=15)
    style(ax, title=title)
    save(fig, path)
    return path