<#assign desc = "Functions"/>
<#function div a b>
    <#return (a / b)?long>
</#function>
<#function ceil a b>
    <#return div(a - 1, b) + 1/>
</#function>
<#function min a b>
    <#return [a, b]?sort?first>
</#function>
<#function max a b>
    <#return [a, b]?sort?last>
</#function>
<#function pow base power>
    <#assign res = 1/>
    <#list 0..<power as it>
        <#assign res = res * base/>
    </#list>
    <#return res>
</#function>
<#function fact n>
    <#assign res = 1/>
    <#list 0..<n as it>
        <#assign res = res * (it + 1)/>
    </#list>
    <#return res>
</#function>
<#function contains items value>
    <#list items as item>
        <#if item == value>
            <#return true/>
        </#if>
    </#list>
    <#return false/>
</#function>
