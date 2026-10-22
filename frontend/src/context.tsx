import { createContext, useContext } from "react";
import type { Client } from "./api/client";

export const ClientContext = createContext<Client | null>(null);

/** The runner client of the page: the token is in it, in memory only. Pages are only shown when there is one. */
export function useClient(): Client {
  const client = useContext(ClientContext);
  if (client === null) {
    throw new Error("no client: the page was rendered without a session token");
  }
  return client;
}
