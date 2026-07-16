<!-- applicable_phases: design_review, diff_review, codebase_review, refactor_design, refactor_code -->

# Correctness & Security

Evaluate whether code is logically correct and free from security vulnerabilities.

**The core question**: Will this code do what the author intended, and can an adversary make it do something else? Correctness errors and security vulnerabilities share a root cause: assumptions about inputs or call contracts that the code fails to verify.

**What to look for**:

- Arguments passed in the wrong order, or with the wrong count or type
- Null/undefined dereferences and off-by-one errors on boundaries
- Missing input validation at trust boundaries (HTTP, env vars, external data)
- Unsanitized data flowing into SQL, shell commands, or rendered output
- Missing or bypassable authentication and authorization checks
- Unbounded resource consumption from user-controlled inputs

**The threshold**: Flag when incorrect code would produce wrong results in reachable scenarios, or when a security gap is exploitable without requiring privileged access. Theoretical issues in unreachable code paths are lower priority.

<design-mode>
When evaluating Code Intent (Design Review phase):

- Does the proposed API have parameters whose ordering could be confused (same type, adjacent position)?
- Does the design validate inputs at trust boundaries before processing?
- Does the design identify who is allowed to call each operation?
- Are resource limits specified for any user-influenced inputs?

Evidence format: Quote the Code Intent description showing the correctness or security gap.
</design-mode>

<code-mode>
When evaluating actual code (Diff Review, Codebase Review, Refactor):

- Are function arguments passed in the right order and with compatible types?
- Are null/undefined values checked before use, and are array bounds respected?
- Is user-supplied data validated before it influences application logic?
- Is external data sanitized before it reaches SQL, shell, or HTML output?
- Are authentication and authorization guards present on every protected route?
- Could a user-controlled value cause unbounded memory or CPU consumption?

Evidence format: Quote code with file:line showing the issue.
</code-mode>

---

## 1. Parameter Contract Violations

<principle>
A call site must satisfy every assumption the callee makes about argument count, order, and type. Parameter mismatches are silent in dynamically-checked positions and catastrophic in positional ones -- the function runs with wrong data and no error is raised.
</principle>

Detect: At every call site, does each argument match the corresponding parameter in position, count, and expected type? Are there adjacent same-typed parameters that could be silently swapped?

<grep-hints>
Pattern indicators (starting points, not definitive):
Functions with 2+ consecutive same-type parameters (`string, string`, `number, number`), destructured object args dropped to positional, overloaded function signatures, API calls whose parameter order differs from a similar function in the same codebase
</grep-hints>

<violations>
Illustrative patterns (not exhaustive -- similar violations exist):

[high] Positional argument swap

- Two adjacent same-typed parameters transposed at call site (e.g., `move(destId, srcId)` when signature is `move(src, dest)`)
- Extra or missing argument leaving a required parameter undefined
- Any call where swapping two args produces a different but plausible result with no type error

[high] Type coercion masking mismatch

- Passing a numeric string where a number is expected, relying on implicit coercion (`"10"` vs `10`)
- Passing `null` where a non-nullable type is assumed internally

[medium] Optional parameter confusion

- Skipping a middle optional parameter without a named argument, shifting remaining positional args
- Providing positional args when the function signature changed to named/object params

[low] Overload ambiguity

- Calling an overloaded function where the resolved overload is not the intended one
</violations>

<exceptions>
Intentional coercions at adapter boundaries where the conversion is explicit and tested. Variadic functions (`...args`) designed to accept variable counts.
</exceptions>

<threshold>
Flag any call site where swapping, adding, or removing an argument produces a plausible alternative interpretation. Same-type adjacent parameters are worth flagging when call-site evidence suggests possible confusion.
</threshold>

## 2. Null, Undefined, and Boundary Safety

<principle>
Code should never assume a value is non-null unless it has been verified or the type system guarantees it. Off-by-one errors and unchecked array access are the arithmetic equivalent: assumptions about range that the code fails to enforce.
</principle>

Detect: Is every value accessed after it could be null/undefined explicitly checked or typed non-nullable? Are loop bounds, slice indices, and array accesses within verified range?

<grep-hints>
Pattern indicators (starting points, not definitive):
`!` non-null assertions, optional chaining `?.` followed by immediate property access on result, `array[index]` without bounds check, `length - 1` in index expressions, `parseInt`/`Number()` without `isNaN` check, `findOne` or `find` result used without null guard
</grep-hints>

<violations>
Illustrative patterns (not exhaustive -- similar violations exist):

[high] Unguarded dereference

- Using `!` non-null assertion on a value that could legitimately be null at runtime
- Calling `.find()` or `.findOne()` and accessing properties on the result without checking for `undefined`
- Accessing `array[0]` on a potentially empty array

[high] Off-by-one

- Loop condition using `<=` vs `<` where the boundary is the last valid index
- Slice/substring end index one past or one short of intended range
- Fence-post errors in pagination (skipping first or last item)

[medium] Unchecked numeric conversion

- `parseInt(str)` or `Number(str)` used without `isNaN()` guard before arithmetic
- Division without checking divisor is non-zero

[low] Implicit falsy short-circuit

- `value && value.prop` where `0` or `""` is a valid value but would be skipped
</violations>

<exceptions>
Non-null assertions backed by a preceding guard that TypeScript cannot narrow (document why). Array access inside a loop where the bound is the array's own `.length`.
</exceptions>

<threshold>
Flag every non-null assertion (`!`) used on a value derived from external data, a database query, or a collection lookup. Flag off-by-one candidates at loop and slice boundaries.
</threshold>

## 3. Input Validation Gaps

<principle>
All data entering the application from outside its trust boundary -- HTTP requests, environment variables, config files, database records from external systems -- must be validated for shape, type, and range before influencing application logic. Unvalidated input is the root cause of most correctness and security bugs.
</principle>

Detect: At every point where external data enters the application, is its shape, type, and range verified before use? Could malformed input reach business logic or persistence?

<grep-hints>
Pattern indicators (starting points, not definitive):
Controller/route handler parameters without DTO class-validator decorators, `req.body`, `req.query`, `req.params` used directly without parsing, `process.env.X` used without type coercion and default, `JSON.parse` without try/catch and schema validation, missing `@IsString()`, `@IsUUID()`, `@Min()`, `@Max()` on DTO fields
</grep-hints>

<violations>
Illustrative patterns (not exhaustive -- similar violations exist):

[high] Missing boundary validation

- HTTP route accepting a body/query/param object with no validation decorators or schema check
- `process.env.PORT` cast directly to number without fallback or range check
- `JSON.parse(externalString)` result used without schema validation (e.g., zod, class-validator)
- Any numeric input from user used as array size, buffer length, or loop bound without max cap

[high] Type assumption without verification

- Treating a query parameter as a number when HTTP delivers strings (e.g., `req.query.limit * 2` without `parseInt`)
- Assuming a UUID is valid format without regex or library check before database lookup

[medium] Missing range/length constraints

- String field with no maximum length limit (potential DoS or truncation bugs)
- Numeric field with no minimum/maximum (negative IDs, impossibly large page sizes)
- Array/collection field with no maximum item count

[low] Lenient parsing accepted silently

- Using `parseInt` with no radix (implicit base-10 assumption, leading zeros can surprise)
- Accepting extra unknown fields without stripping (over-posting / mass assignment risk)
</violations>

<exceptions>
Internal service-to-service calls within the same trust boundary where the caller is verified code. Data that has already been validated at a gateway and is passed via a verified, typed contract.
</exceptions>

<threshold>
Flag any route handler or public-facing function that accepts external data without explicit validation. Missing length/range constraints are medium severity unless the field drives resource allocation.
</threshold>

## 4. Injection and Output Safety

<principle>
Data and code must never be mixed implicitly. When user-supplied data is interpolated into a SQL query, shell command, HTML template, or evaluated expression without escaping or parameterization, the user controls program behavior.
</principle>

Detect: Does any user-supplied value flow -- directly or through intermediate variables -- into a SQL query string, shell command, rendered HTML, file path, or evaluated expression without parameterization or escaping?

<grep-hints>
Pattern indicators (starting points, not definitive):
Template literals containing variables inside `sql\`...\``, `exec(`, `execSync(`, `eval(`, `innerHTML`, `dangerouslySetInnerHTML`, `html\`...\``, string concatenation building SQL or shell strings, `path.join` with user input, `require(variable)`, `import(variable)`
</grep-hints>

<violations>
Illustrative patterns (not exhaustive -- similar violations exist):

[critical] SQL injection

- Raw SQL string concatenation or template literal with unsanitized user input (e.g., `` `SELECT * FROM users WHERE name = '${req.body.name}'` ``)
- ORM escape bypassed via `.execute(rawString)` containing user data
- Any query where user input influences the SQL structure, not just parameter values

[critical] Command injection

- `exec(userInput)`, `execSync(\`cmd ${arg}\`)` where `arg` derives from external input
- `child_process.spawn` with shell: true and unsanitized arguments
- Any shell metacharacter (`; | & $ \` > <`) that could appear in user-controlled string passed to shell

[high] Cross-site scripting (XSS)

- User-supplied string rendered as HTML without encoding (template engines, `innerHTML`, SSR responses)
- `Content-Type: text/html` response containing unescaped user data

[high] Path traversal

- `path.join(baseDir, userInput)` without verifying the resolved path is still inside `baseDir`
- File read/write using a filename constructed from user input without stripping `../` sequences

[medium] Dynamic code evaluation

- `eval(userInput)`, `new Function(userInput)`, `require(userInput)`, dynamic `import(userInput)`
- Template rendering with user-controlled template string (server-side template injection)
</violations>

<exceptions>
ORM parameterized queries where user data is passed as a bound parameter, not interpolated into the query string. Shell commands with fully allowlisted, non-shellable arguments. Static file paths assembled from enum values or configuration, not user input.
</exceptions>

<threshold>
Flag any injection category at [critical] immediately regardless of apparent exploitability. Path traversal and dynamic evaluation are [high] and should be flagged unless an explicit allowlist or `path.resolve` boundary check is present.
</threshold>

## 5. Authentication and Authorization Gaps

<principle>
Every protected operation must verify both identity (authentication) and permission (authorization) before executing. Missing a guard at one endpoint breaks the security model for all users -- the gap is not mitigated by guards on other endpoints.
</principle>

Detect: Is every route or operation that accesses non-public data or performs state changes protected by an authentication guard AND an authorization check appropriate to the resource? Are there endpoints that omit guards present on sibling endpoints?

<grep-hints>
Pattern indicators (starting points, not definitive):
Controller methods without `@UseGuards()`, routes decorated with `@Public()` or `@SkipAuth()` that modify state, authorization checks absent from update/delete handlers that are present on create, `req.user` accessed without guard ensuring it is set, hardcoded credentials or tokens, `BYPASS_AUTH=true` or equivalent in non-dev config
</grep-hints>

<violations>
Illustrative patterns (not exhaustive -- similar violations exist):

[critical] Missing authentication guard

- Route handler with no `@UseGuards()` (or equivalent) that returns or modifies non-public data
- `@Public()` applied to a mutation/write endpoint without documented justification
- `BYPASS_AUTH` or similar flag that could be set in a production environment

[critical] Missing authorization check

- Update or delete handler that validates the resource exists but does not verify the requesting user owns or has permission for it
- Admin-only operation accessible to any authenticated user because role check is absent
- Tenant-scoped resource returned without verifying the caller's tenant matches

[high] Insecure defaults

- New routes added to a controller that has global guard, but the guard is opt-in rather than opt-out (easy to forget)
- Token or secret generated with insufficient entropy (e.g., `Math.random()` for a security token)
- Default account credentials or empty passwords in seed/init scripts not gated to dev-only

[medium] Privilege escalation surface

- User-supplied field that maps to a role or permission stored on the user record, allowing self-elevation
- Indirect object reference that accepts any ID without verifying ownership (IDOR)

[low] Session and token hygiene

- JWT without expiry (`expiresIn` not set)
- Refresh tokens stored in localStorage instead of httpOnly cookies
</violations>

<exceptions>
Explicitly public endpoints (e.g., health check, public catalog). Authentication endpoints themselves (login, register) which by definition cannot require prior authentication.
</exceptions>

<threshold>
Flag missing authentication or authorization at [critical] regardless of how unlikely exploitation seems. Flag insecure defaults at [high]. Flag IDOR and privilege escalation surfaces at [medium] unless user-supplied IDs are already validated against ownership.
</threshold>

## 6. Unsafe Resource Handling

<principle>
Resources allocated in proportion to user-supplied values -- memory buffers, file handles, database connections, loop iterations -- must be capped at safe maxima. Uncapped allocation is a denial-of-service vulnerability; allocation from user-controlled size parameters can also produce memory corruption in native contexts.
</principle>

Detect: Are there any allocations, loops, or recursive calls whose size or depth is controlled by an external input without an explicit upper bound? Could a user cause the process to exhaust memory, CPU, or file descriptors?

<grep-hints>
Pattern indicators (starting points, not definitive):
`Buffer.alloc(userValue)`, `Buffer.allocUnsafe(`, `new Array(userValue)`, `repeat(userValue)`, `take(req.query.limit)` without max cap, recursive functions with user-controlled depth, `while` loops without iteration limit, file uploads without size limit, `Promise.all(userControlledArray)`
</grep-hints>

<violations>
Illustrative patterns (not exhaustive -- similar violations exist):

[high] Unbounded allocation from user input

- `Buffer.alloc(Number(req.body.size))` without max cap
- `new Array(userControlledLength).fill(...)` where length is not capped
- `Buffer.allocUnsafe(n)` -- always prefer `Buffer.alloc`; `allocUnsafe` leaves memory uninitialized

[high] Unbounded iteration

- Pagination `limit` from query parameter with no server-side maximum (e.g., `limit=1000000`)
- `Promise.all(items)` where `items` is a user-supplied list of unbounded length (prefer chunking)
- Recursive processing of user-supplied nested structures without depth limit

[medium] File and connection resource leaks

- File handles opened in a try block but not closed in finally (or without using `using`/`with`)
- Database connections checked out from pool but not released on error paths
- Temp files created on user request without cleanup on completion or failure

[medium] Numeric overflow and coercion

- Integer arithmetic on user-supplied numbers that could overflow safe integer range (`Number.MAX_SAFE_INTEGER`)
- Mixing signed/unsigned intent (e.g., a user-supplied negative offset used as array index)
- `parseInt` result used as buffer size without checking for `NaN` or negative values

[low] Regex denial of service (ReDoS)

- Complex regex with catastrophic backtracking applied to user-supplied strings of arbitrary length
- Nested quantifiers (`(a+)+`, `(.*)*`) in patterns evaluated against user input
</violations>

<exceptions>
Allocations capped by an explicit, code-visible maximum constant. Pagination limits enforced at the DTO layer with `@Max()`. File uploads with `multer` or equivalent enforcing `limits.fileSize`.
</exceptions>

<threshold>
Flag any allocation or iteration whose bound derives from user input without a visible cap at [high]. Flag resource leaks at [medium]. Flag ReDoS candidates at [low] unless the input is also size-capped.
</threshold>
