export function requireAuth(req: any, res: any, next: () => void) {
  if (!req.headers.authorization && !req.session) {
    res.status(401).end();
    return;
  }
  next();
}
