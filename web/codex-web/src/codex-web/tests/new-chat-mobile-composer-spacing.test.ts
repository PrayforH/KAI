import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

describe('新对话移动端输入框间距', () => {
  it('首页只在桌面端增加画布留白', () => {
    const page = source('src/app/chat/page.tsx');
    expect(page).toContain('items-center justify-center overflow-y-auto px-0 py-8 sm:px-6');
  });

  it('空会话不重复叠加手机端横向内边距', () => {
    const chatView = source('src/components/chat/ChatView.tsx');
    expect(chatView).toContain('items-center justify-center px-0 py-8 sm:px-4');
  });
});
