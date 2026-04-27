namespace RoslynService.Models;

public class RelationDto
{
    public string From { get; set; } = "";
    public string To { get; set; } = "";
    public string Type { get; set; } = "";
    public float Confidence { get; set; }
    public string ResolutionType { get; set; } = "semantic";
}
