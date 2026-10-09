import { expect, test } from "vitest";
import { DictationEdit, dictationCaret } from "../src/lib/dictation-edit";

test("whole partials and refined text replace the same selection without duplication", () => {
  const edit = new DictationEdit("前文旧选区后文", 2, 5);
  expect(edit.replace("前文旧选区后文", "实")?.text).toBe("前文实后文");
  expect(edit.replace("前文实后文", "实时文字")?.text).toBe("前文实时文字后文");
  expect(edit.replace("前文实时文字后文", "整理后的文字。")?.text).toBe("前文整理后的文字。后文");
});

test("cancel restores selected text while preserving later edits outside the speech span", () => {
  const edit = new DictationEdit("前文旧选区后文", 2, 5);
  edit.replace("前文旧选区后文", "实时文字");
  expect(edit.replace("新增前文实时文字后文", "新草稿")?.text).toBe("新增前文新草稿后文");
  expect(edit.cancel("新增前文新草稿后文附言")?.text).toBe("新增前文旧选区后文附言");
});

test("manual changes to dictated text win over subsequent partials, refinement and cancellation", () => {
  const edit = new DictationEdit("前后", 1, 1);
  edit.replace("前后", "实时文字");
  expect(edit.replace("前人工文字后", "新的识别文字")).toBeUndefined();
  expect(edit.replace("前人工文字后", "精修结果")).toBeUndefined();
  expect(edit.cancel("前人工文字后")).toBeUndefined();
});

test("typing at the end of a speech span is preserved", () => {
  const edit = new DictationEdit("前文", 2, 2);
  edit.replace("前文", "草稿");
  expect(edit.replace("前文草稿手写", "精修。")?.text).toBe("前文精修。手写");
});

test("an empty speech result rolls back only this recording", () => {
  const edit = new DictationEdit("已经输入的文字", 7, 7);
  edit.replace("已经输入的文字", "临时草稿");
  expect(edit.cancel("已经输入的文字临时草稿")?.text).toBe("已经输入的文字");
});

test("caret follows the changing speech span and retains positions elsewhere", () => {
  const patch = {text:"前文新草稿后文", start:2, end:3, length:3};
  expect(dictationCaret(1, patch)).toBe(1);
  expect(dictationCaret(2, patch)).toBe(5);
  expect(dictationCaret(3, patch)).toBe(5);
  expect(dictationCaret(4, patch)).toBe(6);
});

test("a tail revision leaves selection positions in the unchanged dictated prefix intact", () => {
  const edit = new DictationEdit("前文后文", 2, 2);
  edit.replace("前文后文", "上海的数据，武汉的数");
  const patch = edit.replace("前文上海的数据，武汉的数后文", "上海的数据，武汉的数据。")!;
  expect(patch.start).toBe(12);
  expect(dictationCaret(4, patch)).toBe(4);
  expect(dictationCaret(7, patch)).toBe(7);
  expect(patch.text).toBe("前文上海的数据，武汉的数据。后文");
});
