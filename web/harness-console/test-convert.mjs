import { fromAgUiMessages } from '@assistant-ui/react-ag-ui';
import { readFileSync } from 'node:fs';
const history = JSON.parse(readFileSync('/tmp/hist.json', 'utf8'));
console.log('input messages:', history.messages.length, history.messages.map(m => m.role));
const converted = fromAgUiMessages(history.messages, { showThinking: true });
console.log('converted:', converted.length);
for (const m of converted) console.log(' -', m.role, JSON.stringify(m.content).slice(0, 60));
