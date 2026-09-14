import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { THEMES, getSavedTheme, applyTheme } from '../../utils/theme';

/** 3x2 grid of colored dots previewing a theme's palette. */
function ThemeSwatch({ colors }: { colors: readonly string[] }) {
  return (
    <div className="grid grid-cols-3 gap-[3px]" aria-hidden="true">
      {colors.map((c, i) => (
        <div
          key={i}
          className="w-3 h-3 rounded-full ring-1 ring-border/40"
          style={{ backgroundColor: c }}
        />
      ))}
    </div>
  );
}

interface Props {
  /** Persists the choice with the instance, so every device reaching it follows. */
  onPersist?: (themeId: string) => void;
}

export function ThemeSelector({ onPersist }: Props) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(getSavedTheme);

  const handleChange = (themeId: string) => {
    setCurrent(themeId);
    // Applied first so the change is immediate, then stored: the write is what
    // makes it follow to other devices, not what makes it take effect here.
    applyTheme(themeId);
    onPersist?.(themeId);
  };

  return (
    <fieldset className="flex flex-wrap gap-2 md:grid md:grid-cols-4">
      <legend className="sr-only">{t('settings.local.colorThemeLegend')}</legend>
      {THEMES.map((theme) => (
        <label
          key={theme.id}
          className={
            'flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer border transition-colors focus-within:ring-2 focus-within:ring-ring md:w-full ' +
            (current === theme.id
              ? 'border-primary bg-primary/5'
              : 'border-transparent hover:bg-accent/50')
          }
        >
          <input
            type="radio"
            name="theme"
            value={theme.id}
            checked={current === theme.id}
            onChange={() => handleChange(theme.id)}
            className="sr-only"
          />
          <ThemeSwatch colors={theme.swatches} />
          <span className="text-xs whitespace-nowrap">
            {t(`settings.local.themes.${theme.id}`, { defaultValue: theme.name })}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
