import {createHash} from 'node:crypto';
import type {ProviderConfig} from '../../../../shared/types/model-provider';
import {toolSummary, withoutLocalPaths} from '../privacy-policy';
import {redactSensitiveJson,redactSensitiveText} from '../sensitive-data';
import {array, object, ProviderError, type DshInput} from './protocol';

type ContextSource = '会话及研究编排' | '本地工具摘要' | '运行指令' | '模型历史及工具参数';
export interface ContextItem {
  source: ContextSource;
  text: string;
}
export type ContextReviewer = (
  config: ProviderConfig, items: ContextItem[], signal: AbortSignal,
) => Promise<boolean>;

/** Evaluate every broker request, including retries and automatic tool continuations. */
export function modelContext(input: DshInput, submittedText: string | undefined, strict: boolean, sensitiveFields?: readonly string[]) {
  const extra: ContextItem[] = [];
  const redact = (text: string) => sensitiveFields ? redactSensitiveText(text, sensitiveFields) : text;
  const redactJson = (text: string) => sensitiveFields ? JSON.stringify(redactSensitiveJson(JSON.parse(text), sensitiveFields)) : text;
  const messages = input.messages.map(object);
  // Only the text supplied by the current trusted IPC call is exempt from extra-context review.
  const submittedIndex = submittedText === undefined ? -1 : messages.findLastIndex(message =>
    message.role === 'user' && array(message.content ?? []).some(raw => {
      const block = object(raw);
      return block.type === 'text' && block.text === submittedText;
    }),
  );
  return {
    extra,
    input: {
      ...input,
      messages: messages.map((message, index) => {
        const role = message.role;
        if (role !== 'user' && role !== 'assistant' && role !== 'tool' &&
            role !== 'system' && role !== 'developer') throw new ProviderError('BAD_RESPONSE');
        let hasSubmittedText = false;
        const content = array(message.content ?? []).flatMap(raw => {
          const block = object(raw);
          if (block.type === 'reasoning') return [];
          if (block.type === 'tool-call' && role === 'assistant') {
            if (typeof block.arguments !== 'string') throw new ProviderError('BAD_RESPONSE');
            const args = redactJson(block.arguments);
            extra.push({source: '模型历史及工具参数', text: JSON.stringify({
              name: block.name, arguments: args,
            })});
            return [{...block, arguments: args}];
          }
          if (block.type !== 'text' || typeof block.text !== 'string') throw new ProviderError('UNSUPPORTED');
          let text = block.text;
          if (role === 'tool') {
            text = redactJson(toolSummary(text, strict ? 4000 : 16000));
            extra.push({source: '本地工具摘要', text});
          } else if (role === 'system' || role === 'developer') {
            text = redact(withoutLocalPaths(text));
            if (text) extra.push({source: '运行指令', text});
          } else if (index === submittedIndex && !hasSubmittedText && text === submittedText) {
            hasSubmittedText = true;
          } else if (text) {
            extra.push({source: role === 'user' ? '会话及研究编排' : '模型历史及工具参数', text:redact(text)});
          }
          return [{type: 'text', text:role==='tool'?text:redact(text)}];
        });
        return {role, content, toolCallId: message.toolCallId, isError: message.isError};
      }),
    },
  };
}

export function contextDigest(item: ContextItem): string {
  return createHash('sha256').update(JSON.stringify(item)).digest('hex');
}
