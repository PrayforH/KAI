from harness.dictation.preview import RealtimePreview, overlap_start


def test_sliding_windows_keep_prefix_and_only_revise_the_current_window() -> None:
    preview = RealtimePreview()
    head = "请先查看上海的数据，"
    middle = "然后再查看武汉的数据，"
    tail = "重点关注最近三个月"
    assert preview.update([], head + middle, 290) == head + middle
    assert preview.update([], middle + tail, 1600) == head + middle + tail
    assert preview.update([], middle + tail + "。", 1600) == head + middle + tail
    ending = tail + "，并与上季度对比。"
    assert preview.update([], ending, 3000) == head + middle + ending.rstrip("。")


def test_confirmed_sentences_replace_speculative_preview_without_duplication() -> None:
    preview = RealtimePreview()
    preview.update([], "我想先查看上海然后再查看武汉的数据", 0)
    preview.update([], "然后再查看武汉的数据还要北京的数据", 1000)
    confirmed = "查看上海、武汉和北京的数据。"
    assert preview.update([confirmed], "还需要广州的数据", 4000) == confirmed + "还需要广州的数据"
    ending = "还需要广州的数据。"
    assert preview.update([confirmed, ending], "", 0, final=True) == confirmed + ending


def test_transient_empty_or_uncertain_partial_does_not_erase_the_draft() -> None:
    preview = RealtimePreview()
    original = "希望能完整看到前面的内容"
    preview.update([], original, 0)
    assert preview.update([], "", 8000) == original
    assert preview.update([], "完全没有可靠重叠的结果", 9000) == original
    assert preview.update([], "", 0, final=True) == ""


def test_overlap_ignores_punctuation_and_case_but_rejects_ambiguous_or_short_matches() -> None:
    previous = "第一段。Hello, WORLD! 下一段"
    assert overlap_start(previous, "hello world，新内容") == 4
    assert overlap_start("上海武汉北京上海武汉北京", "上海武汉北京") is None
    assert overlap_start("北京的天气很好", "天气很好") is None
    assert overlap_start("前文İSTANBUL天气", "istanbul的天气") == 2


def test_legacy_full_partials_and_independent_sessions() -> None:
    first, second = RealtimePreview(), RealtimePreview()
    assert first.update([], "临时文字", None) == "临时文字"
    assert first.update([], "修正的文字", None) == "修正的文字"
    assert second.update([], "另一个用户的内容", 0) == "另一个用户的内容"
    assert first.update(["完成的文字。"], "", None, final=True) == "完成的文字。"
    assert second.update([], "", 8000) == "另一个用户的内容"


def test_unconfirmed_terminal_periods_are_deferred_but_confirmed_punctuation_remains() -> None:
    preview = RealtimePreview()
    assert preview.update([], "先看看上海。", 0) == "先看看上海"
    assert preview.update([], "先看看上海和武汉。", 0) == "先看看上海和武汉"
    confirmed = "先看看上海和武汉。"
    assert preview.update([confirmed], "再看看北京。", 4000) == confirmed + "再看看北京"
    ending = "再看看北京。"
    assert preview.update([confirmed, ending], "", 0, final=True) == confirmed + ending
