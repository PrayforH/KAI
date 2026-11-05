"use client";

import { useEffect, useState } from "react";
import { assistantRequest } from "./assistant-client";

export type AppearancePalette = { colors: string[]; avatars: string[] };

let cached: AppearancePalette | null = null;
let inflight: Promise<AppearancePalette | null> | null = null;

/**
 * 取色表由服务端持有：前端不再自己维护一份颜色列表，
 * 免得服务端随机分配时用到的颜色前端根本画不出来。
 */
export function loadAppearancePalette(): Promise<AppearancePalette | null> {
  if (cached) return Promise.resolve(cached);
  if (inflight) return inflight;
  inflight = assistantRequest<AppearancePalette>("specialists/appearance/palette")
    .then(value => {
      if (value && Array.isArray(value.colors) && Array.isArray(value.avatars)) {
        cached = { colors: value.colors, avatars: value.avatars };
      }
      return cached;
    })
    .catch(() => null)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** 组件里用这份清单渲染色板；读不到时只提示，不猜颜色。 */
export function useAppearancePalette(): AppearancePalette & { ready: boolean } {
  const [palette, setPalette] = useState<AppearancePalette | null>(cached);
  useEffect(() => {
    if (cached) {
      setPalette(cached);
      return;
    }
    let alive = true;
    void loadAppearancePalette().then(value => {
      if (alive && value) setPalette(value);
    });
    return () => {
      alive = false;
    };
  }, []);
  return { colors: palette?.colors ?? [], avatars: palette?.avatars ?? [], ready: palette !== null };
}
