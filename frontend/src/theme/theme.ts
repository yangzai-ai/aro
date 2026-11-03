import { useState } from 'react';

export const presets = { it: 'IT 支持', service: '客户服务', general: '通用助手' } as const;
export type Preset = keyof typeof presets;
export type Theme = 'light' | 'dark' | 'system';
export function useTheme() {
  const [theme, updateTheme] = useState<Theme>((document.documentElement.dataset.theme as Theme) || 'system');
  const [preset, updatePreset] = useState<Preset>((document.documentElement.dataset.preset as Preset) || 'it');
  const [saved, setSaved] = useState(true);
  const change = (key: 'theme' | 'preset', value: string) => {
    document.documentElement.dataset[key] = value;
    try { localStorage.setItem(`aro.${key}`, value); } catch { setSaved(false); }
  };
  return { theme, preset, saved,
    setTheme: (value: Theme) => { updateTheme(value); change('theme', value); },
    setPreset: (value: Preset) => { updatePreset(value); change('preset', value); },
  };
}
