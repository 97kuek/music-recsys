import { handle } from '../../gateway.mjs';
export function onRequest(context) {
  return handle(context.request, context.env);
}
