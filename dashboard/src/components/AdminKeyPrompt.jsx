import { useState } from "react";
import { Button } from "./ui";

export function AdminKeyPrompt({ message, onSave }) {
  const [value, setValue] = useState("");
  const submit = (e) => {
    e.preventDefault();
    if (value.trim()) onSave(value.trim());
  };
  return (
    <form className="key-form" onSubmit={submit} aria-label="Admin key">
      <label htmlFor="admin-key-input">Admin key</label>
      <input
        id="admin-key-input" type="password" autoComplete="off" value={value}
        placeholder="Same value as ADMIN_API_KEY on the server"
        onChange={(e) => setValue(e.target.value)}
        aria-describedby="admin-key-help"
      />
      <Button type="submit" variant="primary" disabledReason={value.trim() ? undefined : "Type the key first."}>
        Save key
      </Button>
      <span id="admin-key-help" className="visually-hidden">{message}</span>
    </form>
  );
}
