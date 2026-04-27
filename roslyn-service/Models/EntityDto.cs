namespace RoslynService.Models;

public class EntityDto
{
    public string QualifiedName { get; set; } = "";
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public string? ClassName { get; set; }
    public string? Namespace { get; set; }
    public string FilePath { get; set; } = "";
    public int LineStart { get; set; }
    public int LineEnd { get; set; }
    public string? Signature { get; set; }
    public string? Docstring { get; set; }
    public string Content { get; set; } = "";
    public string? Visibility { get; set; }
    public bool IsAsync { get; set; }
    public bool IsAbstract { get; set; }
    public bool IsInterface { get; set; }
    public string? ReturnType { get; set; }
}
