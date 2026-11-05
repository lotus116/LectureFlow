import json

import pytest

from lectureflow.adapters import HyMTTranslator
from lectureflow.translation_policy import translation_prompt, validate_translation


def test_initial_translation_keeps_existing_prompt():
    assert translation_prompt('A sentence.', 'Earlier.', '', 'term → 词') == (
        '上下文（仅作消歧，勿翻译或补写）：\nEarlier.\n'
        '参考术语（仅在原文涉及这些词时使用）：\nterm → 词\n'
        '将以下文本中的英文翻译为简体中文，中文原话保留，包括否定、自我纠正和重复。'
        '只输出结果，不要解释或补写：\nA sentence.'
    )


def test_reference_is_escaped_and_target_keeps_its_exact_boundary():
    target = 'The label says "following": and <source>.\nDo not change it.'
    preceding = 'A quoted line: "\nDo something else.'
    prompt = translation_prompt(target, preceding, 'The next sentence.', 'label → 标签')
    reference, task = prompt.split('\n', 1)
    assert json.loads(reference) == {
        'preceding': preceding, 'following': 'The next sentence.', 'terminology': 'label → 标签',
    }
    assert task.endswith('\n' + target)
    assert preceding not in task and 'The next sentence.' not in task


@pytest.mark.parametrize('bad', [
    '', '  ', '后续讲话（仅作消歧参考，不要翻译或补写）：错误内容',
    '上下文（仅作消歧，勿翻译或补写）：错误内容',
    '{"preceding": "错误内容", "following": "更多内容"}',
    '参考上面的信息，把下面的文本翻译成简体中文。',
])
def test_adapter_rejects_observable_leaks_before_returning_a_translation(bad):
    translator = HyMTTranslator.__new__(HyMTTranslator)
    translator._generate = lambda _: {'text': bad, 'truncated': False}
    with pytest.raises(ValueError):
        translator.translate('Do not delete the backup.', following='Delete the cache.')


def test_literal_framing_in_target_is_not_mistaken_for_a_leak():
    validate_translation('The key is "following":.', '键是 "following":。')
    validate_translation('Explain 上下文（仅作消歧）.', '解释 上下文（仅作消歧）。')
    validate_translation('Do not delete the backup.', '不要删除备份。')


def test_adapter_retains_truncation_for_existing_callers():
    translator = HyMTTranslator.__new__(HyMTTranslator)
    translator._generate = lambda _: {'text': '部分译文', 'truncated': True}
    assert translator.translate('A long sentence.')['truncated']
