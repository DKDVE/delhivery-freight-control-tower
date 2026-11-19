import { Link } from "react-router-dom";
import { Card, EmptyState } from "@/components/ui";

export default function NotFound() {
  return <Card><EmptyState title="Page not found" hint="Check the address, or go back to the overview." action={<Link className="font-semibold text-navy underline" to="/">Go to Overview</Link>} /></Card>;
}
