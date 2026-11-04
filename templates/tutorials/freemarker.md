**Freemarker** is a language for automating the test generation script, used in Polygon.

A link to the [full documentation](https://freemarker.apache.org/docs/index.html).

A link to basic examples is available on the "Tests" tab.

## Syntax

### Directives

- Written in the format of tags in angle brackets <>;

  - A multi-line directive requires a closing tag starting with a slash.
  
  - For closing a one-line directive the slash is optional.

- Directive names start with #;

- Inside directives you can

  - call other functions, macros;
  
  - refer to variable values and do arithmetic computations.

- Variables / functions are referred to outside directives via ${} (not to be confused with $ — the test number iterator)

  ```
  gen -n=${maxN} > $
  ```

### Script lines

- Each line of the expanded script is a run of a generator (or a chain via `|`)
  with a redirection to the test: `gen -n=5 > $`, `gen -n=5 | upd --rev > $`.

- Generator parameters can't contain quotes (`"`, `'`) and `>`: Polygon doesn't accept
  such lines. There's exactly one `>` in a line — the final `> $`.

- A parameter's value is one word without spaces. Several values —
  separated by commas or as separate parameters:

  ```
  gen -p=${a},${b} > $
  gen -pa=${a} -pb=${b} > $
  ```

  but not `gen -p="${a} ${b}" > $`.

### Variables, literals, basic operations

- Assignment with the assign directive

  ```
  <#assign variable = value/>
  ```

- Strings — in double quotes ```"Hello"```

- Arrays

  - in square brackets ```[1, 5, 100]```
  
  - a contiguous range — with two dots ```10..15```

- Already created variables can be referred to:

  ```
  <#assign print = hello + ", " + "World!"/>
  ```

### Comparisons

- Inside a directive `>` closes the tag: `<#if value >= n>` is read as `<#if value>`
  and the text `= n>`, and Polygon refuses to save the script.

- So comparisons inside directives (`if`, `elseif`, `assign`, `return`, etc.) are written
  only as words, including `<` and `<=` — for uniformity:

  | sign | word |
  |------|-------|
  | `>`  | `gt`  |
  | `>=` | `gte` |
  | `<`  | `lt`  |
  | `<=` | `lte` |

  ```
  <#if value gte n>
  <#assign isSmall = n lt smallN/>
  ```

- `==` and `!=` are written as is.

### The for loop

- Iteration with the list directive

  ```
  <#list values as value>
  	generate -n=${value} > $
  </#list>
  ```

  - values — a value of the "array" type;

  - Nested loops are possible

- Variables may change their state along the way

  ```
  <#assign iteration = iteration + 1/>
  ```

- [A link](https://freemarker.apache.org/docs/ref_directive_list.html) to the documentation.

### The if conditional

- [A link](https://freemarker.apache.org/docs/ref_directive_if.html) to the documentation.

### Functions

- Compute a value from the parameters

- Described with the function directive

  ```
  <#function name param1 param2>
  	<#return result>
  </#function>

  ${name(first, second)}
  ```

- [A link](https://freemarker.apache.org/docs/ref_directive_function.html) to the documentation

### Macros

- Are a subroutine of the script generation;

- Described with the macro directive

  ```
  <#macro name param1 param2>
      generate param1 param2 > $
      generate param2 param1 > $
  </#macro>
  
  <@name 
  	param1 = first
      param2 = second
  >
  ```

  (line breaks in the call are optional)

- [A link](https://freemarker.apache.org/docs/ref_directive_macro.html) to the documentation

## Standard functions

The standard set of script functions (div, ceil, min, max, pow, fact, contains) is
in the attached `test_script_functions.ftl`. This block is copied into every script
in full, without changes, even if some of its functions aren't used:
the author uses them when editing the script by hand.

## An example of a full script for a problem

The input data is assumed to be an array of N integers.

```
<#-- the block from test_script_functions.ftl, in full -->

<#assign rand = "gen_rand"/>
<#assign sort = "sorted"/>

<#assign minN = 1/>
<#assign maxN = 3 * pow(10, 5)/>

<#assign minA = -pow(10, 9)/>
<#assign maxA = pow(10, 9)/>

<#assign description = "Small tests"/>

<#assign smallN = 10/>
<#assign smallA = pow(10, 2)/>

<#list minN..smallN as n>
	${rand} -n=${n} -minA=${-smallA} -maxA=${smallA} > $
    ${rand} -n=${n} -minA=${minA} -maxA=${maxA} > $
</#list>

<#assign description = "Large tests"/>

${rand} -n=${maxN} -minA=${minA} -maxA=${maxA} > $
${rand} -n=${maxN} -minA=${maxA} -maxA=${maxA} > $
${rand} -n=${maxN} -minA=${maxA - smallA} -maxA=${maxA} > $
${rand} -n=${maxN} -minA=${minA} -maxA=${minA} > $

${rand} -n=${maxN} -minA=${minA} -maxA=${minA + smallA} > $

${rand} -n=${maxN} -minA=${minA} -maxA=${maxA} | ${sort} > $
${rand} -n=${maxN} -minA=${minA} -maxA=${maxA} | ${sort} --desc > $
```