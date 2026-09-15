/** Turn `intellectual_property` into `Intellectual property`.
 *
 *  Enum values are the wire format, not a label. Showing them raw makes a careful
 *  system look like a database dump. */
export function humanize(value: string | null | undefined): string {
  if (!value) return "\u2014";
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
