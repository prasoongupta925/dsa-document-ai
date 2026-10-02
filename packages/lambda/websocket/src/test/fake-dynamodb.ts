import type { AttributeValue } from '@aws-sdk/client-dynamodb';

type Item = Record<string, AttributeValue>;
// SDK command inputs of several shapes; the fake reads them loosely
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Input = Record<string, any>;

/**
 * In-memory stand-in for the DynamoDBClient commands the store sends. Query
 * pages the matching items and Scan pages the whole table before filtering
 * (as DynamoDB does), pageSize items per page. BatchWriteItem rejects more
 * than 25 requests or duplicate keys, and leaves the next `unprocessed`
 * requests unprocessed to exercise the retry.
 */
export class FakeDynamoDB {
  readonly calls: { command: string; input: Input }[] = [];
  pageSize = 2;
  unprocessed = 0;
  private readonly tables = new Map<string, Map<string, Item>>();

  constructor(private readonly keyNames: Record<string, [string, string]>) {}

  seed(table: string, items: Item[]) {
    for (const item of items) this.table(table).set(this.id(table, item), item);
  }

  items(table: string): Item[] {
    return [...this.table(table).entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([, item]) => item);
  }

  count(command: string): number {
    return this.calls.filter((call) => call.command === command).length;
  }

  async send(command: unknown): Promise<Input> {
    const name = (command as object).constructor.name;
    const { input } = command as { input: Input };
    this.calls.push({ command: name, input });
    const table = input.TableName as string;
    switch (name) {
      case 'PutItemCommand':
        this.table(table).set(this.id(table, input.Item), input.Item);
        return {};
      case 'GetItemCommand':
        return { Item: this.table(table).get(this.id(table, input.Key)) };
      case 'DeleteItemCommand':
        this.table(table).delete(this.id(table, input.Key));
        return {};
      case 'QueryCommand': {
        const matches = this.condition(input.KeyConditionExpression, input);
        const page = this.page(table, input, this.items(table).filter(matches));
        return {
          ...page,
          Items: page.Items.map((i) => this.project(i, input)),
        };
      }
      case 'ScanCommand': {
        // filter each page, then project, as DynamoDB does
        const page = this.page(table, input, this.items(table));
        const matches = input.FilterExpression
          ? this.condition(input.FilterExpression, input)
          : () => true;
        const Items = page.Items.filter(matches).map((i) =>
          this.project(i, input),
        );
        return { ...page, Items };
      }
      case 'BatchWriteItemCommand':
        return this.batchWrite(input);
      default:
        throw new Error(`FakeDynamoDB: ${name} is not supported`);
    }
  }

  private table(name: string): Map<string, Item> {
    if (!name || !this.keyNames[name]) {
      throw new Error(`FakeDynamoDB: unknown table ${name}`);
    }
    let table = this.tables.get(name);
    if (!table) this.tables.set(name, (table = new Map()));
    return table;
  }

  private id(table: string, item: Item): string {
    const [pk, sk] = this.keyNames[table];
    if (item[pk]?.S === undefined || item[sk]?.S === undefined) {
      throw new Error(`FakeDynamoDB: key ${pk}/${sk} missing`);
    }
    return `${item[pk].S}\n${item[sk].S}`;
  }

  /** `#a = :b` and `begins_with(#a, :b)` terms joined by AND. */
  private condition(expression: string, input: Input) {
    const names = input.ExpressionAttributeNames ?? {};
    const values = input.ExpressionAttributeValues ?? {};
    const terms = expression.split(' AND ').map((term) => {
      const eq = /^(#\w+) = (:\w+)$/.exec(term.trim());
      const begins = /^begins_with\((#\w+), (:\w+)\)$/.exec(term.trim());
      const [, name, value] = eq ?? begins ?? [];
      if (!names[name] || !values[value]) {
        throw new Error(`FakeDynamoDB: cannot evaluate ${term}`);
      }
      const expected = values[value].S as string;
      return (item: Item) => {
        const actual = item[names[name]]?.S;
        if (actual === undefined) return false;
        return eq ? actual === expected : actual.startsWith(expected);
      };
    });
    return (item: Item) => terms.every((term) => term(item));
  }

  private page(table: string, input: Input, items: Item[]) {
    let start = 0;
    if (input.ExclusiveStartKey) {
      // items are sorted by key: continue after the start key, present or not
      const after = this.id(table, input.ExclusiveStartKey);
      start = items.findIndex((item) => this.id(table, item) > after);
      if (start < 0) start = items.length;
    }
    const Items = items.slice(start, start + this.pageSize);
    const [pk, sk] = this.keyNames[table];
    const last = items[start + this.pageSize - 1];
    const LastEvaluatedKey =
      start + this.pageSize < items.length
        ? { [pk]: last[pk], [sk]: last[sk] }
        : undefined;
    return { Items, LastEvaluatedKey };
  }

  private project(item: Item, input: Input): Item {
    if (!input.ProjectionExpression) return item;
    const names = input.ExpressionAttributeNames ?? {};
    const projected: Item = {};
    for (const part of (input.ProjectionExpression as string).split(',')) {
      const attribute = names[part.trim()] ?? part.trim();
      if (item[attribute]) projected[attribute] = item[attribute];
    }
    return projected;
  }

  private batchWrite(input: Input) {
    const UnprocessedItems: Record<string, Input[]> = {};
    for (const [table, requests] of Object.entries(
      input.RequestItems as Record<string, Input[]>,
    )) {
      if (requests.length > 25) {
        throw new Error('FakeDynamoDB: more than 25 requests in a batch');
      }
      const ids = requests.map((request) =>
        this.id(table, request.DeleteRequest?.Key ?? request.PutRequest?.Item),
      );
      if (new Set(ids).size !== ids.length) {
        throw new Error('FakeDynamoDB: duplicate keys in a batch');
      }
      for (const request of requests) {
        if (this.unprocessed > 0) {
          this.unprocessed--;
          (UnprocessedItems[table] ??= []).push(request);
        } else if (request.DeleteRequest) {
          this.table(table).delete(this.id(table, request.DeleteRequest.Key));
        } else {
          const item = request.PutRequest.Item;
          this.table(table).set(this.id(table, item), item);
        }
      }
    }
    return { UnprocessedItems };
  }
}
