'use client';

import { useMemo, useState } from 'react';
import { CaretDown, Check, MagnifyingGlass, X } from '@/components/ui/icon';
import { CodexWebIcon } from '@/components/ui/semantic-icon';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from '@/components/ui/popover';
import { useTranslation } from '@/hooks/useTranslation';
import type { TranslationKey } from '@/i18n';
import { cn } from '@/lib/utils';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

interface NewChatProjectSelectorProps {
  currentProject: string;
  projects: readonly string[];
  onSelectProject: (path: string) => void;
  onClearProject: () => void;
  onCreateProject: () => void;
  mode?: 'code' | 'plan';
  onModeChange?: (mode: 'code' | 'plan') => void;
}

function projectName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

export function NewChatProjectSelector({
  currentProject,
  projects,
  onSelectProject,
  onClearProject,
  onCreateProject,
  mode = 'code',
  onModeChange,
}: NewChatProjectSelectorProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [clearHovered, setClearHovered] = useState(false);

  const uniqueProjects = useMemo(
    () => Array.from(new Set(projects.filter((path) => path.trim()))),
    [projects],
  );
  const filteredProjects = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    if (!normalizedQuery) return uniqueProjects;
    return uniqueProjects.filter((path) =>
      `${projectName(path)} ${path}`.toLocaleLowerCase().includes(normalizedQuery),
    );
  }, [query, uniqueProjects]);

  const openProjectPicker = () => setOpen(true);
  const handleSelect = (path: string) => {
    setOpen(false);
    setQuery('');
    onSelectProject(path);
  };
  const handleCreateProject = () => {
    setOpen(false);
    setQuery('');
    onCreateProject();
  };

  return (
    <div
      data-testid="new-chat-project-selector"
      data-current-project={currentProject || undefined}
      data-mode={mode}
      className="relative z-10 mb-2 flex min-h-8 items-center gap-2 px-3"
    >
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverAnchor asChild>
          <div className="flex min-w-0 items-center text-sm">
            {currentProject ? (
              <>
                <button
                  type="button"
                  aria-label={t('newChat.projectSelector.clear' as TranslationKey)}
                  title={t('newChat.projectSelector.clear' as TranslationKey)}
                  className="group flex size-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted-foreground/10 hover:text-foreground"
                  onClick={onClearProject}
                  onMouseEnter={() => setClearHovered(true)}
                  onMouseLeave={() => setClearHovered(false)}
                  onFocus={() => setClearHovered(true)}
                  onBlur={() => setClearHovered(false)}
                >
                  {clearHovered ? (
                    <X size={12} weight="bold" />
                  ) : (
                    <CodexWebIcon name="folder" size="sm" aria-hidden />
                  )}
                </button>
                <button
                  type="button"
                  className="flex min-w-0 items-center gap-1 rounded-lg px-1 py-1 text-left font-medium text-foreground transition-colors hover:text-primary"
                  onClick={openProjectPicker}
                  aria-label={t('newChat.projectSelector.change' as TranslationKey)}
                >
                  <span className="max-w-48 truncate">{projectName(currentProject)}</span>
                  <CaretDown size={12} className="text-muted-foreground" />
                </button>
              </>
            ) : (
              <button
                type="button"
                className="flex min-w-0 items-center gap-1.5 rounded-lg px-1 py-1 text-muted-foreground transition-colors hover:text-foreground"
                onClick={openProjectPicker}
              >
                <CodexWebIcon name="folder" size="sm" aria-hidden />
                <span>{t('newChat.projectSelector.select' as TranslationKey)}</span>
                <CaretDown size={12} />
              </button>
            )}
          </div>
        </PopoverAnchor>
        <PopoverContent
          side="top"
          align="start"
          sideOffset={6}
          className="w-72 gap-0 overflow-hidden rounded-2xl p-0"
        >
          <div className="flex h-10 items-center gap-2 border-b px-3">
            <MagnifyingGlass size={15} className="shrink-0 text-muted-foreground" />
            <Input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('newChat.projectSelector.search' as TranslationKey)}
              className="h-8 border-0 bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
            />
          </div>
          <div className="max-h-64 overflow-y-auto p-1">
            {filteredProjects.length > 0 ? (
              filteredProjects.map((path) => {
                const selected = path === currentProject;
                return (
                  <button
                    key={path}
                    type="button"
                    data-project-path={path}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent',
                      selected && 'bg-accent/70 text-foreground',
                    )}
                    onClick={() => handleSelect(path)}
                  >
                    <CodexWebIcon name="folder" size="sm" className="shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{projectName(path)}</span>
                    <span className="max-w-28 truncate text-xs text-muted-foreground">{path}</span>
                  </button>
                );
              })
            ) : (
              <div className="px-3 py-4 text-center text-xs text-muted-foreground">
                {t('newChat.projectSelector.noResults' as TranslationKey)}
              </div>
            )}
          </div>
          <div className="border-t p-1">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-accent"
              onClick={handleCreateProject}
            >
              <CodexWebIcon name="folder_add" size="sm" className="text-muted-foreground" aria-hidden />
              <span>{t('newChat.projectSelector.newProject' as TranslationKey)}</span>
            </button>
          </div>
        </PopoverContent>
      </Popover>

      <span className="h-3.5 w-px bg-border/80" aria-hidden />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex h-8 items-center gap-1.5 rounded-lg px-2 text-sm text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
            aria-label={t('newChat.mode.change' as TranslationKey)}
          >
            <CodexWebIcon name="assistant" size="sm" aria-hidden />
            <span>{t(`newChat.mode.${mode}` as TranslationKey)}</span>
            <CaretDown size={12} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-52 rounded-xl p-1.5">
          {(['code', 'plan'] as const).map((option) => (
            <DropdownMenuItem
              key={option}
              onClick={() => onModeChange?.(option)}
              className="min-h-10 rounded-lg px-3"
            >
              <span>{t(`newChat.mode.${option}` as TranslationKey)}</span>
              {mode === option && <Check size={16} className="ml-auto" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
