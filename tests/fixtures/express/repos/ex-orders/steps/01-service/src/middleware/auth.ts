export function requireAuth(req: any, res: any, next: () => void) {
  if (!req.headers.authorization) {
    res.status(401).end();
    return;
  }
  next();
}
