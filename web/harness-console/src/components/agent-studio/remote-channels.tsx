"use client";

import { FeishuConnection } from "./feishu-connection";
import { WeixinConnection } from "./weixin-connection";
import styles from "./remote-channels.module.css";

export function RemoteChannels() {
  return <section className={styles.channels} aria-label="远程通道">
    <header className={styles.header}><h2>远程通道</h2><p>连接微信或飞书，在聊天中使用 KAI 助理。</p></header>
    <div className={styles.grid}><WeixinConnection /><FeishuConnection /></div>
  </section>;
}
