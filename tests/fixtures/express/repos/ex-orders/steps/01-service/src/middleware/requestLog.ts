export function requestLog(req: any, res: any, next: () => void) {
  console.log(req.method, req.url);
  next();
}
