import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

describe('DSH-inspired home shell', () => {
  it('首页启用专用产品外壳并使用紧凑侧栏', () => {
    const appShell = source('src/components/layout/AppShell.tsx');
    expect(appShell).toContain('data-home-shell');
    expect(appShell).toContain('CHATLIST_DEFAULT = 276');
  });

  it('侧栏以任务入口和项目列表为主', () => {
    const sidebar = source('src/components/layout/ChatListPanel.tsx');
    expect(sidebar).toContain('data-dsh-sidebar');
    expect(sidebar).toContain('MonolithIcon');
    expect(sidebar).toContain('chatList.newConversation');
  });

  it('首页输入框使用独立的视觉层', () => {
    const input = source('src/components/chat/MessageInput.tsx');
    const styles = source('src/app/globals.css');
    expect(input).toContain('data-chat-composer');
    expect(styles).toContain('[data-home-shell] [data-chat-composer]');
    expect(styles).toContain('--dsh-accent');
  });
});
