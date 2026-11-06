import pytest

from lectureflow.translation_numbers import repair_numbers


@pytest.mark.parametrize(
    ("source", "translation", "expected"),
    [
        ("Point zero six. Point zero two four.", "0.06。0.24。", "0.06。0.024。"),
        ("minus zero point zero zero eight", "负0.08", "负0.008"),
        ("one seventy-five billion", "一百七十五亿", "1750亿"),
        ("one hundred and twenty million items", "有120万项", "有12000万项"),
        ("12 billion, not twenty", "需要12亿，而非二十", "需要120亿，而非二十"),
        ("3 trillion", "3亿", "30000亿"),
    ],
)
def test_only_patch_the_supported_quantity(source, translation, expected):
    assert repair_numbers(source, translation) == expected


@pytest.mark.parametrize(
    ("source", "translation"),
    [
        ("point zero two four", "0.024"),
        ("point zero two four", "数值为0.34"),
        ("point zero two four", "换算后是0.24‰"),
        ("The dropout probability is point zero five.", "丢弃概率为0.5成。"),
        ("The threshold is point zero two four.", "阈值为0.24 × 10⁻¹。"),
        ("The probability was point zero zero five.", "换成百分比是0.5。"),
        ("point zero zero three", "以千分率计为3。"),
        ("point zero two four", "阈值为0.24e-1。"),
        ("point zero two four metres", "2.4厘米"),
        ("one point zero two four", "第一个权重是0.24"),
        ("point zero two four and point two four", "一个是0.24，另一个也是0.24"),
        ("a billion", "大约十亿"),
        ("one hundred and seventy five billion", "一千七百五十亿"),
        ("two or three billion", "两三亿"),
        ("one billion and one million", "十亿零一百万"),
        ("a quarter million", "二十五万"),
        ("two point five million", "两点五万"),
        ("175 billion", "一七五亿"),
        ("five billion", "一七五亿"),
        ("one billion", "一万亿"),
        ("five million", "1.5万美元"),
        ("五亿", "五亿"),
        ("175 billion", "一百七十五个十亿"),
    ],
)
def test_preserve_correct_ambiguous_or_unsupported_values(source, translation):
    assert repair_numbers(source, translation) == translation
