**Freemarker** - язык для автоматизации скрипта генерации тестов, используемый в Polygon.

Ссылка на [полную документацию](https://freemarker.apache.org/docs/index.html).

Ссылка на базовые примеры доступна на вкладке “Tests”.

## Синтаксис

### Команды

- Пишутся в формате тегов в угловых скобках <>;

  - Многострочная команда требует закрывающий тег, начинающийся со слеша.
  
  - Для закрытия однострочной команды слеш опционален.

- Имена команд начинаются с #;

- Внутри команд можно

  - вызывать другие функции, макросы;
  
  - обращаться к значения переменных и производить арифметические вычисления.

- Обращение к переменным / функциям вне команд - через ${} (не путаем с $ - итератором номера теста)

  ```
  gen -n=${maxN} > $
  ```

### Переменные, литералы, базовые операции

- Присваивание командой assign

  ```
  <#assign variable = value/>
  ```

- Строки - в двойных кавычках ```“Hello”```

- Массивы

  - в квадратных скобках ```[1, 5, 100]```
  
  - непрерывный отрезок - через две точки ```10..15```

- Можно обращаться к уже созданным переменным:

  ```
  <#assign print = hello + ", “ + “World!”/>
  ```

### Цикл for

- Итерация командой list

  ```
  <#list values as value>
  	generate -n=${value} > $
  </#list>
  ```

  - values - значение типа “массив”;

  - Можно делать вложенные циклы

- Переменные могут менять состояние в процессе

  ```
  <#assign iteration = iteration + 1/>
  ```

- [Ссылка](https://freemarker.apache.org/docs/ref_directive_list.html) на документацию.

### Условный оператор if

- [Ссылка](https://freemarker.apache.org/docs/ref_directive_if.html) на документацию.

### Функции

- Вычисляют значение на основе параметров

- Описываются командой function

  ```
  <#function name param1 param2>
  	<#return result>
  </#function>

  ${name(first, second)}
  ```

- [Ссылка](https://freemarker.apache.org/docs/ref_directive_function.html) на документацию

### Макросы

- Являются подпрограммой генерации скрипта;

- Описываются командой macro

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

  (переводы строк при вызове опциональны)

- [Ссылка](https://freemarker.apache.org/docs/ref_directive_macro.html) на документацию

## Стандартные функции

Стандартный набор функций скрипта (div, ceil, min, max, pow, fact, contains) —
в приложенном `test_script_functions.ftl`. Этот блок целиком, без изменений
копируется в каждый скрипт, даже если часть функций в нём не используется:
автор пользуется ими при ручной доработке скрипта.

## Пример полного скрипта для задачи

Предполагается, что входными данными является массив из N целых чисел.

```
<#-- блок из test_script_functions.ftl, целиком -->

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