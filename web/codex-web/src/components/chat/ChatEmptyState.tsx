'use client';

import { Button } from '@/components/ui/button';
import { useTranslation } from '@/hooks/useTranslation';

interface ChatEmptyStateProps {
  hasProvider: boolean;
}

export function ChatEmptyState({ hasProvider }: ChatEmptyStateProps) {
  const { t } = useTranslation();

  if (hasProvider) return null;

  return (
    <div className="flex items-center justify-center gap-3 px-4 py-2 text-center">
      <p className="text-sm text-muted-foreground">{t('chat.empty.noProvider')}</p>
      <Button
        size="sm"
        variant="outline"
        onClick={() => window.location.assign('/settings/codex')}
      >
        {t('chat.empty.openSetup')}
      </Button>
    </div>
  );
}
