"""Os utilitários de teste desenham o que as regiões dizem (o plano 2 compara texto e imagem)."""

from PIL import ImageDraw

import synth


class _Recorder:
    """ImageDraw que anota as palavras desenhadas."""

    def __init__(self, draw: ImageDraw.ImageDraw) -> None:
        self.draw = draw
        self.words: list[str] = []

    def text(self, xy, text, **kw) -> None:
        self.words.extend(text.split())
        self.draw.text(xy, text, **kw)

    def __getattr__(self, name):
        return getattr(self.draw, name)


def test_region_text_is_the_drawn_text(monkeypatch):
    recorders: list[_Recorder] = []
    real = ImageDraw.Draw

    def recording(img):
        recorders.append(_Recorder(real(img)))
        return recorders[-1]

    monkeypatch.setattr(synth.ImageDraw, "Draw", recording)
    _, regions = synth.benjamin_like_page()
    assert [w for r in regions for w in r.text.split()] == recorders[0].words
