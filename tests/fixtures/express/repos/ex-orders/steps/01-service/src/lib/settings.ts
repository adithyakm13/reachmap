export function get(name: string) {
  return process.env[name];
}
